"""P0 course ingestion: checkpointed local ZIP imports and evidence-linked procedures.

Never executes course files or instructions from course contents. Each ZIP member is
its own searchable source; extracted steps are *candidates*, not verified methods.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from .store import Store, now
from .import_formats import MEDIA_EXTENSIONS, IMAGE_EXTENSIONS

COURSE_EXTENSIONS = (MEDIA_EXTENSIONS | IMAGE_EXTENSIONS |
    {'.txt', '.md', '.markdown', '.rst', '.csv', '.json', '.yaml', '.yml',
     '.srt', '.vtt', '.pdf', '.epub', '.html', '.htm', '.docx', '.pptx',
     '.xlsx', '.odt', '.py', '.cpp', '.cc', '.c', '.h',
     '.hpp', '.rs', '.js', '.ts', '.tsx', '.java', '.cs', '.sh', '.bat'})
MAX_COURSE_ENTRIES = 12000
MAX_EXPANDED_BYTES = 12 * 1024**3
MAX_MEMBER_BYTES = 4 * 1024**3
MAX_ZIP_BYTES = 16 * 1024**3
MAX_PROCEDURES = 200
PATTERNS = {
    'Memory scanning': r'\b(?:memory scan|first scan|next scan|value scan)\b',
    'Pointer resolution': r'\b(?:pointer chain|base address|pointer scan)\b',
    'Restart verification': r'\b(?:restart|relaunch|after reboot)\b',
    'UI navigation': r'\b(?:click|open the menu|select the tab|press the button)\b',
    'Request inspection': r'\b(?:http request|intercept|proxy history|repeater)\b',
    'Result verification': r'\b(?:verify|validate|compare the result|read back)\b',
    'Code execution': r'\b(?:execute|compile|run the script|run the program)\b',
}

ACTION_RE = re.compile(
    r'\b(?:step\s*\d+|first|next|then|finally|open|click|select|navigate|'
    r'press|type|enter|run|execute|compile|search|scan|find|inspect|'
    r'compare|verify|restart|check|save|set|change|attach|breakpoint|'
    r'install|import|export|drag|choose|observe|look for|use the)\b', re.I)
NUMBERED_RE = re.compile(r'^\s*(?:\d{1,3}[.)]|[-*•])\s+')


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _safe_items(archive: zipfile.ZipFile):
    items = archive.infolist()
    if len(items) > MAX_COURSE_ENTRIES:
        raise ValueError('Course archive contains too many entries (12,000 maximum).')
    total = 0
    names = set()
    for item in items:
        name = item.filename
        parts = PurePosixPath(name)
        if (not name or parts.is_absolute() or '..' in parts.parts or '\\' in name
                or re.match(r'^[A-Za-z]:', name) or any(ord(c) < 32 for c in name)):
            raise ValueError(f'Unsafe course member path: {name[:120]!r}')
        if name in names or stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError('Course archive contains a duplicate name or symbolic link.')
        names.add(name)
        if item.is_dir():
            continue
        if item.file_size > MAX_MEMBER_BYTES:
            raise ValueError('Course member exceeds 4 GiB.')
        if item.file_size > 8 * 1024**2 and item.file_size > max(1, item.compress_size) * 200:
            raise ValueError('Course archive contains an excessive compression ratio.')
        total += item.file_size
        if total > MAX_EXPANDED_BYTES:
            raise ValueError('Course archive exceeds the 12 GiB expanded-data limit.')
    return items


class CourseP0:
    def __init__(self, knowledge, start_worker: bool = True):
        self.knowledge = knowledge
        self.store: Store = knowledge.store
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        if start_worker:
            for batch in self.store.list('course_batch'):
                if batch.get('status') in {'queued', 'importing'}:
                    self._launch(batch['id'])

    def import_archive(self, path: str, project_id=None):
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_file() or candidate.suffix.lower() != '.zip':
            raise ValueError('Select the absolute path of an existing local ZIP file.')
        if candidate.stat().st_size > MAX_ZIP_BYTES:
            raise ValueError('Course ZIP exceeds the 16 GiB archive limit.')
        if project_id is not None and not self.store.get('project', project_id):
            raise ValueError('Unknown knowledge project.')
        with zipfile.ZipFile(candidate) as z:
            items = _safe_items(z)
            total = sum(i.file_size for i in items if i.filename.lower().endswith(tuple(COURSE_EXTENSIONS)))
        snap = candidate.stat()
        ident = _sha(f'{candidate}:{snap.st_size}:{snap.st_mtime_ns}:{project_id or ""}')[:24]
        batch_id = 'course_' + ident
        existing = self.store.get('course_batch', batch_id)
        if existing:
            if existing['status'] in {'queued', 'importing', 'interrupted', 'partial'}:
                self._launch(batch_id)
            return {**existing, 'duplicate': True}
        batch = self.store.put('course_batch', {'id':batch_id, 'title': candidate.stem,
            'path':str(candidate), 'project_id':project_id,
            'archive_size':snap.st_size, 'archive_mtime_ns':snap.st_mtime_ns,
            'status':'queued', 'total_entries':len(items), 'total_bytes':total,
            'completed_entries':0, 'imported_sources':0, 'errors':[], 'items':[],
            'detail':'Waiting to import course files'})
        self._launch(batch_id)
        return batch

    def list_batches(self, project_id='*'):
        items = self.store.list('course_batch')
        if project_id != '*':
            items = [b for b in items if b.get('project_id') in (None, project_id)]
        # The overview polls frequently. Do not send thousands of member records
        # to the UI every five seconds; the detailed status endpoint retains them.
        return [{k:v for k,v in b.items() if k != 'items'} for b in items]

    def status(self, id: str):
        value = self.store.get('course_batch', id)
        if not value: raise ValueError('Course not found.')
        return value

    def resume(self, id: str):
        batch = self.status(id)
        if batch['status'] == 'complete': return batch
        self._launch(id)
        return self.status(id)

    def _launch(self, id):
        with self._lock:
            thread = self._threads.get(id)
            if thread and thread.is_alive(): return
            thread = threading.Thread(target=self.run_archive, args=(id,), daemon=True,
                                      name='course-import-'+id[-8:])
            self._threads[id] = thread
            thread.start()

    def _update(self, id, **updates):
        old = self.status(id)
        return self.store.put('course_batch', {**old, **updates})

    def run_archive(self, id: str):
        batch = self.status(id)
        archive_path = Path(batch['path'])
        errors = list(batch.get('errors', []))
        reports = {row['member']:row for row in batch.get('items', [])}
        root = self.store.root / 'sources' / id
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            current = archive_path.stat()
            if (current.st_size != batch['archive_size'] or
                    current.st_mtime_ns != batch['archive_mtime_ns']):
                raise ValueError('The course ZIP changed after import started. Import it again.')
            self._update(id, status='importing', detail='Reading archived lessons')
            with zipfile.ZipFile(archive_path) as archive:
                members = _safe_items(archive)
                for index, item in enumerate(members):
                    if item.is_dir() or '__MACOSX' in PurePosixPath(item.filename).parts:
                        continue
                    ext = PurePosixPath(item.filename).suffix.lower()
                    if ext not in COURSE_EXTENSIONS or item.flag_bits & 1:
                        reports[item.filename] = {'member':item.filename,'state':'skipped',
                                                  'reason':'Unsupported or encrypted file'}
                        continue
                    previous = reports.get(item.filename)
                    if previous and previous.get('state') in {'queued', 'ready', 'partial'} and self.store.get('source',previous.get('source_id','')):
                        continue
                    try:
                        target_dir = root / f'{index:05d}_{_sha(item.filename)[:12]}'
                        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                        basename = PurePosixPath(item.filename).name
                        dest = target_dir / basename
                        temporary = target_dir / (basename+'.part')
                        if not dest.exists() or dest.stat().st_size != item.file_size:
                            with archive.open(item) as src, temporary.open('wb') as out:
                                written = 0
                                while block := src.read(1024*1024):
                                    written += len(block)
                                    if written > item.file_size: raise ValueError('Member exceeded declared size.')
                                    out.write(block)
                            if written != item.file_size: raise ValueError('Incomplete archive member.')
                            temporary.replace(dest)
                            dest.chmod(0o600)
                        # A caption sidecar is extracted into its own entry by the loop.
                        # For the matching video, also place it beside the video if known.
                        if ext in MEDIA_EXTENSIONS:
                            for suffix in ('.srt', '.vtt'):
                                sibling = str(PurePosixPath(item.filename).with_suffix(suffix))
                                try: side = archive.getinfo(sibling)
                                except KeyError: continue
                                if not side.flag_bits & 1 and side.file_size <= 20*1024*1024:
                                    target_side = dest.with_suffix(suffix)
                                    if not target_side.exists():
                                        target_side.write_bytes(archive.read(side))
                                    break
                        source = self.knowledge.import_source(path=str(dest),copy_original=False,project_id=batch.get('project_id'))
                        self.knowledge._update(source['id'],course_batch_id=id,
                                               course_member=item.filename,course_path=batch['title'])
                        reports[item.filename] = {'member':item.filename, 'state':'queued',
                                                  'bytes':item.file_size,'source_id':source['id']}
                        if len(reports) <= 5 or len(reports) % 5 == 0:
                            self._update(id, items=list(reports.values()),
                                         completed_entries=len(reports),
                                         imported_sources=sum(bool(r.get('source_id')) for r in reports.values()),
                                         detail=f'Queued {basename} ({len(reports)}/{len(members)})')
                    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                        reports[item.filename] = {'member':item.filename,'state':'error','reason':str(exc)[:250]}
                        errors.append(f'{item.filename}: {str(exc)[:200]}')
                        # A corrupt lesson must not block all following course lessons.
                        self._update(id, items=list(reports.values()),completed_entries=len(reports),
                                     imported_sources=sum(bool(r.get('source_id')) for r in reports.values()),
                                     errors=errors[-100:],detail=f'Cannot import {item.filename}')
            self._update(id, status='partial' if errors else 'complete',
                         detail='Course sources queued for indexing', errors=errors,
                         items=list(reports.values()),completed_entries=len(reports))
        except Exception as exc:
            errors.append(str(exc)[:450])
            self._update(id,status='interrupted',detail=str(exc)[:450],errors=errors,
                         items=list(reports.values()),completed_entries=len(reports))
        return self.status(id)


class ProcedureP0:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.store = knowledge.store

    def build(self, source_id: str):
        source = self.store.get('source', source_id)
        if not source: raise ValueError('Source not found.')
        if source.get('status') not in {'ready','partial','processing'}:
            raise ValueError('The source must be indexed before extracting procedures.')
        with self.store.connection() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT id,ordinal,text,locator,modality,metadata FROM knowledge_chunks WHERE source_id=? ORDER BY ordinal',
                (source_id,))]
        # Only quote actual indexed text, never synthesize clicks or shell commands.
        procedures = []
        grouped: list[dict] = []
        def flush():
            if not grouped: return
            if len(procedures) >= MAX_PROCEDURES: return
            first = grouped[0]
            label = re.sub(r'\s+', ' ', first['quote'])[:100]
            pid = 'procedure_'+_sha(source_id+':'+first['chunk_id']+':'+first['quote'])[:24]
            steps=[]
            for step_idx,item in enumerate(grouped):
                steps.append({'id':pid+f'_step_{step_idx+1}', 'number':step_idx+1,
                    'quote':item['quote'], 'chunk_id':item['chunk_id'],
                    'locator':item['locator'], 'modality':item['modality'],
                    'timestamp_seconds':item.get('timestamp_seconds'),
                    'evidence_type':'observed_description' if item['modality']=='vision' else 'quoted_source',
                    'verified':False})
            procedures.append({'id':pid,'source_id':source_id,'project_id':source.get('project_id'),
                'title':label, 'status':'candidate', 'reviewed':False,'verified':False,
                'steps':steps, 'origin':'quoted_course_material',
                'limitations':'Source descriptions and sampled frames are not independent execution evidence.'})
        for row in rows:
            metadata = json.loads(row['metadata'] or '{}')
            # One or two lines from a passage can be an instruction, never the whole
            # unbounded paragraph. Chunk ID + verbatim quote is durable evidence.
            lines = [line.strip() for line in row['text'].splitlines() if line.strip()]
            for line in lines:
                if len(line) < 12 or not (ACTION_RE.search(line) or NUMBERED_RE.match(line)):
                    continue
                if not ACTION_RE.search(line) and len(line) > 250: continue
                quote = line[:450] if len(line)>450 else line
                # Refuse truncated quotes: a partial command is not valid provenance.
                if len(line) > 450: continue
                item = {'quote':quote, 'chunk_id':row['id'],
                        'locator':row['locator'],'modality':row['modality'],
                        'timestamp_seconds':metadata.get('start')}
                if grouped and (row['ordinal'] > grouped[-1]['ordinal'] + 1 or len(grouped)>=6):
                    flush(); grouped=[]
                item['ordinal']=row['ordinal']
                grouped.append(item)
                if len(procedures) >= MAX_PROCEDURES: break
            if len(procedures) >= MAX_PROCEDURES: break
        flush()
        with self.store.connection(immediate=True) as db:
            # Rebuild is idempotent and invalidates old candidates (never user-verified data).
            old = db.execute('SELECT id FROM entities WHERE kind=? AND json_extract(data,\'$.source_id\')=?',
                             ('course_procedure',source_id)).fetchall()
            for row in old:
                db.execute('DELETE FROM entities WHERE kind=? AND id=?',('course_procedure',row['id']))
            db.execute("DELETE FROM knowledge_edges WHERE source_id=? AND relation IN ('documents_procedure','procedure_step','step_evidence','step_next','mentions_pattern')",(source_id,))
            db.execute("DELETE FROM knowledge_nodes WHERE kind IN ('course_procedure','course_step') AND json_extract(data,'$.source_id')=?",(source_id,))
            for proc in procedures:
                stamp=now()
                db.execute('INSERT INTO entities VALUES(?,?,?,?,?)',
                           (proc['id'],'course_procedure',json.dumps(proc),stamp,stamp))
                self.knowledge._node(db,proc['id'],'course_procedure',proc['title'],
                                     {'source_id':source_id,'status':'candidate','verified':False})
                self.knowledge._edge(db,source_id,proc['id'],'documents_procedure',source_id,None,
                                      {'origin':'course_p0','status':'candidate'})
                last=None
                for step in proc['steps']:
                    self.knowledge._node(db,step['id'],'course_step',step['quote'][:160],
                                        {'source_id':source_id,'chunk_id':step['chunk_id'],
                                         'locator':step['locator'],'status':'quoted_not_tested'})
                    self.knowledge._edge(db,proc['id'],step['id'],'procedure_step',source_id,step['chunk_id'],
                                         {'origin':'course_p0','order':step['number']})
                    self.knowledge._edge(db,step['id'],step['chunk_id'],'step_evidence',source_id,step['chunk_id'],
                                         {'origin':'verbatim','locator':step['locator']})
                    if last:
                        self.knowledge._edge(db,last,step['id'],'step_next',source_id,step['chunk_id'],
                                             {'origin':'sequence_in_source'})
                    # Cross-course pattern links are lexical observations, not proof
                    # that the source demonstrated or successfully executed a method.
                    for label,pattern in PATTERNS.items():
                        if re.search(pattern,step['quote'],re.I):
                            pattern_id='course_pattern_'+_sha(label)[:20]
                            self.knowledge._node(db,pattern_id,'course_pattern',label,
                                {'status':'inferred','origin':'lexical','verified':False})
                            self.knowledge._edge(db,step['id'],pattern_id,'mentions_pattern',source_id,step['chunk_id'],
                                {'origin':'lexical','status':'inferred','locator':step['locator']})
                    last=step['id']
        return {'source_id':source_id,'status':'candidate','procedures':len(procedures),
                'steps':sum(len(p['steps']) for p in procedures)}

    def list(self, source_id=None, project_id='*', limit=150):
        procedures=self.store.list('course_procedure')
        if source_id:
            procedures=[p for p in procedures if p.get('source_id')==source_id]
        if project_id!='*':
            procedures=[p for p in procedures if p.get('project_id') in (None,project_id)]
        return procedures[:max(1,min(int(limit),500))]

    def get(self,id):
        value=self.store.get('course_procedure',id)
        if not value: raise ValueError('Procedure not found.')
        return value
