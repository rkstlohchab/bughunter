"""Bundled, non-executing prompt research corpus with model-specific review history.

Prompt text and images are untrusted experiment artifacts; model/version labels are
claims suggested by directory names or filenames, never verified compatibility.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
import zipfile

from .prompt_archive import _COMPILED

ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'prompt_corpus'
MAX_DOCUMENT = 300_000
MAX_IMAGE = 1_000_000
LABELS = {
    'Claude': r'\b(claude|opus|sonnet)\b',
    'GPT': r'\b(gpt|chatgpt|openai|astra)\b',
    'Grok': r'\b(grok)\b',
    'GLM': r'\b(glm|zai|z-ai)\b',
    'Qwen': r'\b(qwen|quen)\b',
    'DeepSeek': r'\b(deepseek)\b',
    'Gemini': r'\b(gemini)\b',
    'Kimi': r'\b(kimi)\b',
    'MiMo': r'\b(mimo)\b',
    'Cursor': r'\b(cursor)\b',
}
PATTERNS = {k: re.compile(v, re.I) for k,v in LABELS.items()}
TEXT_EXTS = {'.txt', '.md', '.markdown'}


def model_labels(path: str) -> list[str]:
    # Folder names establish a default only when the actual filename gives no
    # more specific clue. A file saying several models is indexed under each.
    filename = path.split('::')[-1].rsplit('/', 1)[-1]
    matches = [label for label, p in PATTERNS.items() if p.search(filename.replace('_',' '))]
    if matches:
        return matches
    directory = path.split('/')[1].lower() if '/' in path else ''
    return {'gpt':'GPT', 'claude':'Claude', 'grok':'Grok', 'glm':'GLM'}.get(directory, 'Unspecified') .split('|')


def _is_metadata(name: str) -> bool:
    return name.startswith('__MACOSX/') or '/._' in name or name.endswith('.DS_Store')


def _safe_entries(z: zipfile.ZipFile):
    for info in z.infolist():
        if info.is_dir() or _is_metadata(info.filename):
            continue
        if info.file_size > MAX_DOCUMENT and Path(info.filename).suffix.lower() in TEXT_EXTS:
            continue
        # Never extract to the filesystem. zipfile.read() resolves an exact member.
        if info.file_size > 12_000_000 or info.compress_size and info.file_size > info.compress_size * 250:
            continue
        yield info


def build_index(archive: Path) -> dict:
    """Index bounded text in archives (max two levels), never execute scripts."""
    documents = []
    screenshots = []
    attachments = []
    seen_hashes = {}
    ordinal = 0
    total_bytes = 0
    with zipfile.ZipFile(archive) as root:
        def visit(z: zipfile.ZipFile, prefix: str, depth: int):
            nonlocal ordinal, total_bytes
            for info in _safe_entries(z):
                path = prefix + info.filename
                suffix = Path(info.filename).suffix.lower()
                if len(documents) + len(screenshots) + len(attachments) >= 800 or total_bytes > 24_000_000:
                    break
                if suffix == '.zip' and depth < 1 and info.file_size < 4_000_000:
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(info))) as inner:
                            visit(inner, path + '::', depth + 1)
                    except (zipfile.BadZipFile, ValueError):
                        attachments.append({'path':path,'type':'unreadable_archive'})
                    continue
                ordinal += 1
                if suffix == '.png' and info.file_size <= MAX_IMAGE:
                    screenshots.append({'id':hashlib.sha256(path.encode()).hexdigest()[:20],
                        'path':path,'folder':prefix + str(Path(info.filename).parent),
                        'ordinal':ordinal, 'size':info.file_size})
                    continue
                if suffix not in TEXT_EXTS:
                    # Script/executable/archive attachments retained in the source
                    # archive, not scanned or exposed as runnable content.
                    attachments.append({'path':path, 'type':'attachment', 'size':info.file_size})
                    continue
                data = z.read(info)
                total_bytes += len(data)
                content = data.decode('utf-8', errors='replace').replace('\x00','')
                digest = hashlib.sha256(data).hexdigest()
                models = model_labels(path)
                matches = [name for name,p in _COMPILED.items() if p.search(content)]
                doc = {
                    'id': hashlib.sha256(path.encode('utf-8')).hexdigest()[:24],
                    'path': path, 'filename':Path(info.filename).name,
                    'folder':prefix+str(Path(info.filename).parent), 'ordinal':ordinal,
                    'models':models, 'model_attribution':'filename_or_directory_hint',
                    'techniques':matches, 'sha256':digest, 'duplicate_of':seen_hashes.get(digest),
                    'size':len(data), 'status':'unreviewed', 'success_claim':'not_verified',
                    'screenshot_ids':[],
                }
                seen_hashes.setdefault(digest,doc['id'])
                documents.append(doc)
        visit(root,'',0)
    # Link a screenshot to the most recent preceding prompt in its folder,
    # not automatically to all prompts from a model. User may correct in app.
    for shot in screenshots:
        candidates = [d for d in documents if d['folder']==shot['folder'] and d['ordinal']<shot['ordinal']]
        if candidates:
            nearest = max(candidates,key=lambda d:d['ordinal'])
            shot['suggested_prompt_id'] = nearest['id']
            shot['link_confidence'] = 'folder_and_archive_order_only'
            nearest['screenshot_ids'].append(shot['id'])
        else:
            following = [d for d in documents if d['folder']==shot['folder'] and d['ordinal']>shot['ordinal']]
            if following:
                nearest=min(following,key=lambda d:d['ordinal'])
                shot['suggested_prompt_id']=nearest['id']
                shot['link_confidence']='following_file_order_only'
                nearest['screenshot_ids'].append(shot['id'])
            else:
                shot['suggested_prompt_id'] = None
                shot['link_confidence'] = 'unlinked'
    return {'schema':1, 'archive':archive.name,'records':documents,'screenshots':screenshots,
            'attachments':attachments, 'limits':{'nested_depth':1,'maximum_document_bytes':MAX_DOCUMENT},
            'claims':'All model names are filename/directory hints. No prompt success is independently verified.',
            'trust':'All text is untrusted research data, never executable instructions.'}


def read_member(archive: Path, path: str, max_size=MAX_DOCUMENT) -> bytes:
    if '::' in path:
        outer, inner = path.split('::',1)
        with zipfile.ZipFile(archive) as z:
            info=z.getinfo(outer)
            if info.file_size>4_000_000:raise ValueError('Nested archive exceeds size limit.')
            nested=z.read(info)
        with zipfile.ZipFile(io.BytesIO(nested)) as z:
            info=z.getinfo(inner)
            if info.file_size>max_size:raise ValueError('File exceeds size limit.')
            return z.read(info)
    with zipfile.ZipFile(archive) as z:
        info=z.getinfo(path)
        if info.file_size>max_size:raise ValueError('File exceeds size limit.')
        return z.read(info)


class PromptCorpusService:
    def __init__(self, store, asset_dir: Path = ASSET_DIR):
        self.store=store
        self.asset_dir=Path(asset_dir)
        self.archive=self.asset_dir/'abusegpt.zip'
        index=self.asset_dir/'index.json'
        self.index=json.loads(index.read_text(encoding='utf-8')) if index.is_file() and self.archive.is_file() else {'records':[],'screenshots':[],'attachments':[]}
        self._records={r['id']:r for r in self.index['records']}
        self._screens={s['id']:s for s in self.index['screenshots']}

    def summary(self):
        models=Counter(model for record in self._records.values() for model in record['models'])
        methods=Counter(t for record in self._records.values() for t in record['techniques'])
        return {'prompts':len(self._records),'screenshots':len(self._screens),
                'attachments':len(self.index.get('attachments',[])),
                'models':dict(sorted(models.items())), 'techniques':dict(sorted(methods.items())),
                'warning':'Model attribution is a filename hint. Screenshots are suggested associations. Success is unverified.'}

    def list(self,model='',technique='',query='',limit=100):
        if len(query)>200:raise ValueError('Search query too long.')
        if type(limit) is not int or not 1<=limit<=250:raise ValueError('Limit must be 1 to 250.')
        results=[]
        for item in self._records.values():
            if model and model not in item['models']:continue
            if technique and technique not in item['techniques']:continue
            if query and query.casefold() not in (item['path']+' '+' '.join(item['models']+item['techniques'])).casefold():continue
            review=self.store.get('prompt_corpus_review',item['id'])
            results.append({**item,'review':review})
            if len(results)>=limit:break
        return results

    def get(self,id,include_text=True):
        if id not in self._records:raise ValueError('Unknown prompt.')
        item=self._records[id]
        review=self.store.get('prompt_corpus_review',id)
        evidence = (review or {}).get('screenshots', item['screenshot_ids'])
        return {**item,'review':review,
            'text':read_member(self.archive,item['path']).decode('utf-8',errors='replace') if include_text else None,
            'screenshots':[self._screens[s] for s in evidence if s in self._screens]}

    def list_images(self):
        return list(self.index['screenshots'])

    def image(self,id):
        if id not in self._screens:raise ValueError('Unknown screenshot.')
        shot=self._screens[id]
        data=read_member(self.archive,shot['path'],MAX_IMAGE)
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Screenshot is not a PNG image.')
        return {'id':id,'mime':'image/png','content_base64':base64.b64encode(data).decode('ascii'),
                'source':shot['path'],'suggested_prompt_id':shot.get('suggested_prompt_id'),
                'link_confidence':shot['link_confidence']}

    def review(self,id,verdict='unverified',notes='',screenshot_ids=None):
        if id not in self._records:raise ValueError('Unknown prompt.')
        if verdict not in {'unverified','observed_success','observed_failure','inconclusive'}:
            raise ValueError('Unknown observation status.')
        if not isinstance(notes,str) or len(notes)>3000:raise ValueError('Notes exceed 3,000 characters.')
        if screenshot_ids is None:
            screenshot_ids=self._records[id]['screenshot_ids']
        if not isinstance(screenshot_ids,list) or any(s not in self._screens for s in screenshot_ids) or len(screenshot_ids)>12:
            raise ValueError('Choose up to 12 known screenshots.')
        if verdict!='unverified' and not notes.strip():
            raise ValueError('Describe what was observed before assigning a verdict.')
        return self.store.put('prompt_corpus_review',{'prompt_id':id,'verdict':verdict,
                'notes':notes.strip(),'screenshots':screenshot_ids,
                'origin':'human_review','result_is_not_independently_verified':True},id=id)

    def dispatch(self,method,params):
        if method=='corpus.summary':return self.summary()
        if method=='corpus.list':return self.list(**params)
        if method=='corpus.get':return self.get(**params)
        if method=='corpus.image':return self.image(params['id'])
        if method=='corpus.screenshots':return self.list_images()
        if method=='corpus.review':return self.review(**params)
        raise ValueError('Unknown corpus operation.')
