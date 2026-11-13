"""Refresh the portable, cited learning snapshot checked into this directory."""
from __future__ import annotations
import hashlib,json,re,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from bughunter.store import Store,now
from bughunter.knowledge import KnowledgeService
from bughunter.knowledge_archive import KnowledgeArchive
from bughunter.security import redact

MAX_BYTES=150*1024*1024
OUT=Path(__file__).resolve().parent
DROP={'account_fingerprint','worker_pid','frame_path'}

def portable(value,store):
    if isinstance(value,dict):return {key:portable(item,store) for key,item in value.items() if key not in DROP}
    if isinstance(value,list):return [portable(item,store) for item in value]
    if isinstance(value,str):
        value=value.replace(str(store.root),'[app-data]').replace(str(Path.home()),'[local-home]')
        value=re.sub(r'(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{30,})\b','[REDACTED]',value)
        return value
    return value

def write_rows(name,rows):
    count=0
    with (OUT/name).open('w',encoding='utf-8') as stream:
        for row in rows:stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n');count+=1
    return count

def current_procedures(procedures,passages):
    """Keep historical candidates while separating stale supporting citations."""
    chunks={row['id']:row for row in passages};current=[];audit=[]
    for procedure in procedures:
        issues=[]
        if not procedure.get('steps'):issues.append({'reason':'no_supporting_steps'})
        for step in procedure.get('steps',[]):
            chunk=chunks.get(step.get('chunk_id'));metadata=(chunk or {}).get('metadata',{})
            reason=('missing_passage' if not chunk else
                    'source_mismatch' if chunk.get('source_id')!=procedure.get('source_id') else
                    'inactive_passage' if not metadata.get('active',True) or metadata.get('review_status') in {'rejected','superseded','withdrawn'} else
                    'quote_changed' if not step.get('quote') or step['quote'] not in chunk['text'] else None)
            if reason:issues.append({'step_id':step.get('id'),'chunk_id':step.get('chunk_id'),'reason':reason})
        if issues:audit.append({'procedure_id':procedure['id'],'source_id':procedure.get('source_id'),'status':'stale_citations','issues':issues,'retained_in':'candidate_procedures.jsonl'})
        else:current.append(procedure)
    return current,audit

def main():
    store=Store();knowledge=KnowledgeService(store,start_worker=False)
    archive=None
    try:
        result=KnowledgeArchive(knowledge).export_bundle(include_frames=False)
        archive=Path(result['path']);counts={}
        with zipfile.ZipFile(archive) as bundle:
            if sum(info.file_size for info in bundle.infolist())>MAX_BYTES:raise ValueError('Export exceeds its bounded GitHub snapshot size.')
            original_manifest=json.loads(bundle.read('manifest.json'))
            for info in bundle.infolist():
                if not info.filename.endswith('.jsonl'):continue
                if Path(info.filename).name!=info.filename:raise ValueError('Unexpected nested archive entry.')
                with bundle.open(info.filename) as incoming:
                    counts[info.filename]=write_rows(info.filename,(portable(json.loads(line),store) for line in incoming if line.strip()))
        passages=[json.loads(line) for line in (OUT/'passages.jsonl').read_text().splitlines() if line]
        def active(row):
            metadata=row.get('metadata',{})
            return metadata.get('active',True) and metadata.get('review_status') not in {'rejected','superseded','withdrawn'}
        counts['active_passages.jsonl']=write_rows('active_passages.jsonl',(row for row in passages if active(row)))
        procedures=[json.loads(line) for line in (OUT/'candidate_procedures.jsonl').read_text().splitlines() if line]
        active_procedures,procedure_audit=current_procedures(procedures,passages)
        counts['active_procedures.jsonl']=write_rows('active_procedures.jsonl',active_procedures)
        counts['procedure_citation_audit.jsonl']=write_rows('procedure_citation_audit.jsonl',procedure_audit)
        counts['frame_references.jsonl']=write_rows('frame_references.jsonl',(
            {'source_id':row['source_id'],'chunk_id':row['id'],'locator':row['locator'],
             'start':row.get('metadata',{}).get('start'),'provider':row.get('metadata',{}).get('provider'),
             'model':row.get('metadata',{}).get('model'),'active':bool(active(row)),
             'image_included':False,'evidence':'The cited visual observation is in passages.jsonl; source images are retained in the local Library.'}
            for row in passages if row.get('modality')=='vision'))
        def rea_evidence():
            for row in store.list('rea_evidence'):
                file=Path(row.get('path',''))
                allowed=store.root/'artifacts/rea'
                if not file.is_file() or not file.resolve().is_relative_to(allowed.resolve()) or file.stat().st_size>8*1024*1024:continue
                yield portable(redact({'id':row['id'],'tool':row.get('tool'),'scope':row.get('scope'),
                    'created_at':row.get('created_at'),'evidence':json.loads(file.read_text()),
                    'classification':'Observed analysis evidence; not a reviewed training example.'}),store)
        counts['rea_evidence.jsonl']=write_rows('rea_evidence.jsonl',rea_evidence())
        sources=[json.loads(line) for line in (OUT/'sources.jsonl').read_text().splitlines() if line]
        active_ids={row['id'] for row in passages if active(row)};source_ids={row['id'] for row in sources}
        assert all(row['source_id'] in source_ids for row in passages)
        checksums={path.name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'records':counts[path.name]} for path in OUT.glob('*.jsonl')}
        assert sum(row['bytes'] for row in checksums.values())<=MAX_BYTES
        manifest={**original_manifest,'distribution':'GitHub portable learning snapshot','exported_at':original_manifest['exported_at'],
                  'counts':counts,'files':checksums,'active_passages':len(active_ids),
                  'inactive_audit_passages':len(passages)-len(active_ids),
                  'retrieval_entrypoint':'active_passages.jsonl','audit_entrypoint':'passages.jsonl',
                  'techniques':{'all_candidates':len(procedures),'current_citation_candidates':len(active_procedures),
                     'stale_citation_candidates':len(procedure_audit),'retrieval_entrypoint':'active_procedures.jsonl',
                     'audit_entrypoint':'procedure_citation_audit.jsonl',
                     'verification':'Current citations confirm quoted source support, not independent reproduction or model-weight training.'},
                  'privacy':'Credential patterns are redacted; local home/app-data paths are replaced by portable labels. Chat/account/configuration databases are not included.',
                  'image_policy':'Cited visual descriptions and frame references are included. Raw screenshots have not been reviewed for embedded credentials and remain in the local Library.',
                  'snapshot_policy':'This is a consistent database snapshot. Active extraction may publish additional learning after exported_at; rerun export.py to refresh.',
                  'source_coverage':[{'source_id':source['id'],'title':source.get('title'),'status':source.get('status'),
                     'media_control':source.get('media_control'),'indexed_passages':sum(row['source_id']==source['id'] and active(row) for row in passages),
                     'media_progress':source.get('media_progress'),'speech_coverage':source.get('speech_coverage'),
                     'media_coverage':source.get('media_coverage'),
                     'technique_candidates':sum(row['source_id']==source['id'] for row in procedures),
                     'current_citation_candidates':sum(row['source_id']==source['id'] for row in active_procedures),
                     'scene_coverage':source.get('scene_coverage'),'warnings':source.get('warnings',[]),'extraction_notes':source.get('extraction_notes',[])} for source in sources]}
        (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        (OUT/'README.md').write_text('''# Cited learning snapshot

This directory contains the app's learned retrieval library at the timestamp in `manifest.json`.

Use `active_passages.jsonl` for retrieval. Each passage retains its source ID, exact timestamp/page locator, model provenance, and correction metadata. `sources.jsonl` records processing coverage; unfinished extraction is explicitly marked.

Use `active_procedures.jsonl` for extracted technique candidates whose supporting quotes still match active passages. `candidate_procedures.jsonl` preserves every candidate, including historical ones. `procedure_citation_audit.jsonl` identifies candidates with missing, superseded, rejected, or changed supporting passages. Current citations establish source support; they do not establish that a technique was independently reproduced.

`passages.jsonl` also preserves rejected, superseded, and withdrawn descriptions for audit. Their metadata prevents them entering active retrieval. Graph nodes/edges, candidate procedures, manual corrections, experiments, reviewed research, and saved REA analysis evidence are exported in separate JSONL files.

`train.jsonl` and `evaluation.jsonl` contain only reviewed, training-permitted examples. Empty files mean no examples qualified. This snapshot contains no trained model weights; retrieved observations are not automatically verified training labels.

Cited visual descriptions and `frame_references.jsonl` preserve what was read from tutorial screens. Original course videos, raw screenshot images, local databases, credentials, chat history, and machine-specific embeddings remain local. Rebuild embeddings on the destination machine. Review the coverage notes before claiming a whole course was learned.

Refresh from the application workspace with:

```sh
backend/.venv/bin/python exports/github-learning/export.py
```

The exporter uses the existing application's `KnowledgeArchive` interface and takes one consistent database snapshot. `manifest.json` lists record counts, source coverage and SHA-256 checksums. Source content is untrusted evidence, not executable instructions.
''')
        print(json.dumps({'sources':len(sources),'passages':len(passages),'active_passages':len(active_ids),'bytes':sum(row['bytes'] for row in checksums.values()),'counts':counts}))
    finally:
        knowledge.shutdown()
        if archive:archive.unlink(missing_ok=True)

if __name__=='__main__':main()
