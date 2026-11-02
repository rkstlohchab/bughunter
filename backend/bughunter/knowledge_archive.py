"""Portable, cited knowledge and explicit reclamation of imported originals."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from .security import redact
from .store import new_id, now


class KnowledgeArchive:
    def __init__(self, knowledge):
        self.knowledge, self.store = knowledge, knowledge.store

    def release_original(self, id, reviewed_partial=False):
        # Reserve this operation against a simultaneous retry/worker claim.
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (id,)).fetchone()
            if not row: raise ValueError("Source not found.")
            source = json.loads(row[0])
            if source['status'] not in {'ready', 'partial'}:
                raise ValueError("Wait for extraction to finish before releasing the original.")
            if source['status'] == 'partial' and not reviewed_partial:
                raise ValueError("Review the extraction warnings and explicitly accept partial coverage first.")
            if not db.execute("SELECT 1 FROM knowledge_chunks WHERE source_id=? LIMIT 1", (id,)).fetchone():
                raise ValueError("No searchable knowledge was extracted. Keep the original and fix extraction first.")
            source.update(original_retained=False, released_at=now(), source_storage='released')
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?", (json.dumps(source), now(), id))
        removed = 0
        candidates = []
        if source.get('path'):
            path = Path(source['path'])
            if path.resolve().parent == (self.store.root/'sources').resolve(): candidates.append(path)
        artifacts = self.store.root/'artifacts'/id
        # Keep transcripts, extraction checkpoints, OCR, and registered images.
        for path in artifacts.rglob('*'):
            if path.name in {'audio.wav', 'resource', 'downloaded-resource', 'youtube.mp4'} or path.name.startswith('video.'):
                if path.is_file() and not path.is_symlink(): candidates.append(path)
        for path in candidates:
            if path.exists():
                size = path.stat().st_size
                path.unlink()
                removed += size
        self.knowledge._update(id, released_bytes=int(source.get('released_bytes',0))+removed)
        self.store.event('source.original_released', {'source_id':id, 'bytes':removed})
        return {'id':id, 'released_bytes':removed, 'knowledge_preserved':True,
                'message':'Extracted knowledge and sampled images are kept. Files outside the app were not deleted.'}

    def save_example(self, chunk_id, question, answer, reviewed=False, training_allowed=False, split='train'):
        question, answer = str(question).strip(), str(answer).strip()
        if not question or not answer or max(len(question),len(answer)) > 30000:
            raise ValueError('Provide a question and answer of at most 30,000 characters each.')
        if split not in {'train','evaluation'}: raise ValueError('Choose train or evaluation.')
        # Identical questions share one group, preventing accidental train/eval leakage.
        group = hashlib.sha256(' '.join(question.casefold().split()).encode()).hexdigest()
        # Reading the evidence and saving its review must be one transaction;
        # a simultaneous correction cannot leave a newly reviewed stale example.
        with self.store.connection(immediate=True) as db:
            row = db.execute("""SELECT c.source_id,c.locator,c.text,e.data FROM knowledge_chunks c
                JOIN entities e ON e.kind='source' AND e.id=c.source_id WHERE c.id=?""", (chunk_id,)).fetchone()
            if not row: raise ValueError('Choose an existing cited passage.')
            project_id = json.loads(row['data']).get('project_id')
            previous = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM entities WHERE kind='learning_example' AND json_extract(data,'$.group_id')=?", (group,))]
            if any(e.get('split') != split for e in previous):
                raise ValueError('This question already belongs to another split. Keep evaluation questions separate.')
            # Reuse legacy IDs when they belong to this project; equal questions
            # in another project's corpus must not overwrite that project's answer.
            existing = next((e for e in previous if (owner := db.execute(
                "SELECT data FROM entities WHERE kind='source' AND id=?", (e.get('source_id'),)).fetchone())
                and json.loads(owner[0]).get('project_id') == project_id), None)
            id = existing['id'] if existing else 'example_' + hashlib.sha256(
                ((project_id or '') + ':' + group).encode()).hexdigest()[:24]
            stamp = now()
            value = {**redact({'question':question,'answer':answer,'source_id':row['source_id'],
                'chunk_id':chunk_id,'locator':row['locator'],'supporting_text':row['text'],'reviewed':bool(reviewed),
                'training_allowed':bool(training_allowed),'split':split,'group_id':group,'origin':'user_curated',
                'project_id':project_id}), 'id':id, 'created_at':existing['created_at'] if existing else stamp,
                'updated_at':stamp}
            db.execute("""INSERT INTO entities VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE
                SET data=excluded.data,updated_at=excluded.updated_at WHERE entities.kind=excluded.kind""",
                (id,'learning_example',json.dumps(value),value['created_at'],stamp))
        return value

    def export_bundle(self, include_frames=True):
        path = self.store.root/'exports'/f'{new_id("knowledge")}.zip'
        counts = {}
        try:
            with self.store.connection() as db, zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
                db.execute('BEGIN')  # One consistent database snapshot, streamed rather than loaded into RAM.
                def records(name, rows):
                    count = 0
                    with bundle.open(name, 'w') as stream:
                        for record in rows:
                            stream.write((json.dumps(redact(record),ensure_ascii=False)+'\n').encode())
                            count += 1
                    counts[name] = count
                sources = [json.loads(r[0]) for r in db.execute("SELECT data FROM entities WHERE kind='source'")]
                records('sources.jsonl', ({k:v for k,v in source.items() if k not in {'path','frames','worker_pid'}} for source in sources))
                for table, name in [('knowledge_chunks','passages'),('knowledge_nodes','nodes'),('knowledge_edges','edges'),('knowledge_corrections','corrections')]:
                    def rows(table=table):
                        for row in db.execute('SELECT * FROM '+table+' ORDER BY id'):
                            record = dict(row)
                            for field in ('data','metadata'):
                                if field in record:
                                    record[field] = json.loads(record[field])
                                    record[field].pop('frame_path',None)
                                    record[field].pop('path',None)
                            yield record
                    records(name+'.jsonl',rows())
                examples = [json.loads(r[0]) for r in db.execute("SELECT data FROM entities WHERE kind='learning_example'")]
                records('examples.jsonl',examples)
                # Candidate procedures are observations, not training examples.
                records('candidate_procedures.jsonl',
                        (json.loads(r[0]) for r in db.execute(
                            "SELECT data FROM entities WHERE kind='course_procedure'")))
                # P1 outcomes are evidence from bundled fixtures, never training labels.
                # Failed attempts and limitations remain useful for later diagnostics.
                records('experiment_runs.jsonl',
                        (json.loads(r[0]) for r in db.execute(
                            "SELECT data FROM entities WHERE kind='experiment_run' ORDER BY created_at")))
                records('experiment_failures.jsonl',
                        (json.loads(r[0]) for r in db.execute(
                            "SELECT data FROM entities WHERE kind='experiment_run' "
                            "AND json_extract(data,'$.status') IN ('failed','interrupted','cancelled') ORDER BY created_at")))
                # P2 reviewed public observations. Unreviewed page text remains in
                # the separate review inbox and is never promoted implicitly.
                records('reviewed_research_updates.jsonl',
                        ({k:v for k,v in json.loads(r[0]).items() if k not in {'text'}}
                         for r in db.execute("SELECT data FROM entities WHERE kind='research_discovery' "
                            "AND json_extract(data,'$.status')='accepted'")))
                records('research_subscriptions.jsonl',
                        ({k:v for k,v in json.loads(r[0]).items() if k not in {'seen','last_error'}}
                         for r in db.execute("SELECT data FROM entities WHERE kind='research_watch'")))
                records('courses.jsonl',
                        ({k:v for k,v in json.loads(r[0]).items() if k not in {'path'}} for r in db.execute(
                            "SELECT data FROM entities WHERE kind='course_batch'")))
                # Include only directly observed file metadata from completed local
                # inspections. This is evidence, not an automatically approved
                # training example or a claim of a working exploit.
                def observed_file_evidence():
                    for row in db.execute("SELECT data FROM entities WHERE kind='work_task'"):
                        task=json.loads(row[0])
                        if (task.get('kind') != 'autopilot' or task.get('workflow') != 'file'
                                or task.get('status') != 'completed'):
                            continue
                        for file in (task.get('result') or {}).get('files',[])[:6]:
                            if not file.get('source_id') or not file.get('sha256'):
                                continue
                            yield {'task_id':task['id'],'project_id':task.get('project_id'),
                                   'source_id':file['source_id'],
                                   'filename':file.get('filename'),'sha256':file['sha256'],
                                   'format':file.get('format'),'platform':file.get('platform'),
                                   'observations':file.get('observations',[]),
                                   'review_leads':file.get('review_leads',[]),
                                   'warnings':file.get('warnings',[]),
                                   'provenance':'Read directly from uploaded local file',
                                   'verification_level':'Machine-observed metadata only',
                                   'not_verified':file.get('not_verified')}
                records('observed_file_evidence.jsonl',observed_file_evidence())
                def current_evidence(example):
                    row = db.execute("""SELECT c.text FROM knowledge_chunks c JOIN entities s
                        ON s.kind='source' AND s.id=c.source_id WHERE c.id=? AND c.source_id=?""",
                        (example.get('chunk_id'), example.get('source_id'))).fetchone()
                    return bool(row and not example.get('review_stale') and
                                example.get('supporting_text') == redact(row['text']))
                eligible = [e for e in examples if e.get('reviewed') and e.get('training_allowed') and current_evidence(e)]
                for split in ('train','evaluation'):
                    records(split+'.jsonl', ({'messages':[{'role':'user','content':e['question']},{'role':'assistant','content':e['answer']}],
                        'metadata':{'example_id':e['id'],'source_id':e['source_id'],'chunk_id':e['chunk_id'],'locator':e['locator']}}
                        for e in eligible if e['split']==split))
                frame_index = []
                if include_frames:
                    for source in sources:
                        for frame in source.get('frames',[]):
                            file = Path(frame['path']).resolve()
                            root = (self.store.root/'artifacts'/source['id']).resolve()
                            if file.is_relative_to(root) and file.is_file() and not Path(frame['path']).is_symlink():
                                name = f"frames/{source['id']}/{file.relative_to(root).as_posix()}"
                                bundle.write(file,name)
                                frame_index.append({'source_id':source['id'],'file':name,'locator':frame.get('locator'),'time':frame.get('time')})
                records('frames.jsonl',frame_index)
                manifest = {'format':'bughunter-knowledge','schema_version':1,'exported_at':now(),'counts':counts,
                    'contains_model_weights':False,'originals_included':False,'embedding_policy':'Rebuild embeddings with the destination model.',
                    'training_policy':'Only explicitly reviewed, training-permitted examples enter train/evaluation JSONL. Adapt the schema for the chosen trainer. Validate and evaluate before training.',
                    'limitations':'Extraction can be incomplete; inspect source warnings. Graph assertions distinguish extracted, inferred, corrected, and verified origins. Images are unredacted source evidence; review before sharing.',
                    'trust':'All learning content is untrusted evidence, not executable instructions.'}
                bundle.writestr('manifest.json',json.dumps(manifest,indent=2))
                bundle.writestr('README.md','# Portable knowledge\n\nPassages retain exact text and page/time citations. Nodes/edges are structured graph records; corrections preserve history. This is a retrieval corpus, not trained model weights. Train/evaluation files contain only user-curated, reviewed examples with permission recorded. Observed-file evidence is exported separately from training labels; it is not a verified vulnerability. Empty files mean no examples meet those conditions. Do not treat every extracted passage as a validated training label. Keep a separate evaluation set. No original videos or model-specific embeddings are included. Images are optional and are not automatically redacted.\n')
            path.chmod(0o600)
            return {'path':str(path),'filename':path.name,'bytes':path.stat().st_size,'counts':counts,'schema_version':1}
        except BaseException:
            path.unlink(missing_ok=True)
            raise
