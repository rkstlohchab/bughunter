"""P2 auditable training/evaluation export, deduplication and leakage quarantine.

Only explicitly reviewed and training-permitted source-backed examples qualify.
This exports datasets, not model weights and not machine-generated labels.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .security import redact_text
from .store import new_id, now

MAX_ROWS = 5000
MAX_TEXT = 24000


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.casefold())).strip()


class DatasetsP2:
    def __init__(self, store):
        self.store = store

    def consent(self, id, allowed):
        if type(allowed) is not bool: raise ValueError('Consent must be boolean.')
        row = self.store.get('training_example',id)
        if not row: raise ValueError('Training example not found.')
        if row.get('status') != 'reviewed' and allowed:
            raise ValueError('Review the example before approving it for training.')
        return self.store.put('training_example',{**row,'training_allowed':allowed},id=id)

    def _chunk_ok(self, entry):
        with self.store.connection() as db:
            row = db.execute('SELECT source_id,original_text,locator FROM knowledge_chunks WHERE id=?',
                             (entry.get('chunk_id'),)).fetchone()
        return bool(row and row['source_id'] == entry.get('source_id') and
                    row['locator'] == entry.get('locator') and
                    (not entry.get('supporting_text') or entry['supporting_text'] == row['original_text']))

    def _reference_ok(self, value):
        if not isinstance(value,str) or not value.strip(): return False
        if value == 'self:manual': return True  # Explicit human-authored record.
        for prefix,kind in (('tor_page:','tor_page'),):
            if value.startswith(prefix):
                entity=self.store.get(kind,value[len(prefix):])
                return bool(entity and (kind != 'tor_page' or entity.get('reviewed')))
        return bool(self.store.get('source',value))

    def _rows(self, project_id):
        rows, excluded = [], []
        all_learning = self.store.list('learning_example')
        all_training = self.store.list('training_example')
        for e in all_learning:
            source = self.store.get('source',e.get('source_id',''))
            if project_id != '*' and (not source or source.get('project_id') not in (None,project_id)):
                continue
            reason = None
            if not e.get('reviewed'): reason = 'not_reviewed'
            elif not e.get('training_allowed'): reason = 'no_training_permission'
            elif not self._chunk_ok(e): reason = 'citation_changed_or_missing'
            elif not source: reason = 'missing_source'
            if reason:
                excluded.append({'id':e['id'],'reason':reason})
                continue
            rows.append({'id':e['id'],'prompt':e['question'],'answer':e['answer'],
                         'split':e.get('split','train'),'family':e.get('source_id'),
                         'question_key':sha(norm(e['question'])),'source':e['source_id'],
                         'citation':{'source_id':e['source_id'],'chunk_id':e['chunk_id'],'locator':e['locator']},
                         'origin':'learning_example'})
        for e in all_training:
            if project_id != '*' and e.get('project_id') not in (None,project_id):
                continue
            reason = None
            if e.get('domain') == 'ai_redteam': reason = 'retired_domain'
            elif e.get('status') != 'reviewed': reason = 'not_reviewed'
            elif e.get('training_allowed') is not True: reason = 'no_training_permission'
            elif not self._reference_ok(e.get('source')): reason = 'missing_source_reference'
            if reason:
                excluded.append({'id':e['id'],'reason':reason})
                continue
            # Training workspaces do not currently expose split choice; only
            # training, never evaluation, unless a separate reviewed example
            # is explicitly marked as evaluation in the Knowledge editor.
            rows.append({'id':e['id'],'prompt':e['instruction'] + (('\n\nReference context (data):\n'+e['context']) if e.get('context') else ''),
                         'answer':e['response'],'split':'train',
                         'family':e.get('group') or e.get('source') or e['id'],
                         'question_key':sha(norm(e['instruction'])),'source':e['source'],
                         'citation':{'source_ref':e['source']},'origin':'training_example'})
        return rows[:MAX_ROWS],excluded

    @staticmethod
    def _audit(rows,excluded):
        # Union related scenarios and all repeated normalized questions.
        # This catches train/eval leakage across different source references.
        parent=list(range(len(rows)))
        def root(i):
            while parent[i]!=i:
                parent[i]=parent[parent[i]]
                i=parent[i]
            return i
        def union(i,j):
            a,b=root(i),root(j)
            if a!=b: parent[b]=a
        families={}; questions={}; answer_keys={}
        for i,r in enumerate(rows):
            for table,key in ((families,r['family']),(questions,r['question_key']),
                              (answer_keys,sha(norm(r['prompt']+'\x00'+r['answer'])))):
                if key in table: union(i,table[key])
                else: table[key]=i
        groups=defaultdict(list)
        for i in range(len(rows)):groups[root(i)].append(i)
        accepted=[]
        quarantined=list(excluded)
        for indices in groups.values():
            decisions={rows[i]['split'] for i in indices}
            if not decisions <= {'train','evaluation'} or len(decisions)!=1:
                quarantined.extend({'id':rows[i]['id'],'reason':'split_leakage_conflict'} for i in indices)
                continue
            # Duplicate instruction/response pairs within the same split are
            # excluded deterministically, not added twice to training data.
            seen=set()
            for i in sorted(indices,key=lambda n:rows[n]['id']):
                row=rows[i]
                key=sha(norm(row['prompt'])+'\0'+norm(row['answer']))
                if key in seen:
                    quarantined.append({'id':row['id'],'reason':'duplicate_pair'})
                else:
                    seen.add(key)
                    accepted.append(row)
        accepted.sort(key=lambda r:(r['split'],r['id']))
        return accepted,quarantined

    def inspect(self,project_id='*'):
        if project_id != '*' and not self.store.get('project',project_id):
            raise ValueError('Project not found.')
        rows,excluded=self._rows(project_id)
        approved,quarantine=self._audit(rows,excluded)
        counts={name:sum(x['split']==name for x in approved) for name in ('train','evaluation')}
        reasons={}
        for x in quarantine:reasons[x['reason']]=reasons.get(x['reason'],0)+1
        return {'eligible':len(approved),'counts':counts,'excluded':len(quarantine),
                'exclusion_reasons':reasons,'independent_evaluation_available':counts['evaluation']>0,
                'dataset_ready':bool(counts['train'] and counts['evaluation']),
                'note':'Only permissioned, reviewed examples with valid provenance are exported. An evaluation set is never synthesized.'}

    def export(self,project_id='*'):
        rows,excluded=self._rows(project_id)
        approved,quarantine=self._audit(rows,excluded)
        if not approved: raise ValueError('No reviewed and permitted examples with valid source evidence are available.')
        folder=self.store.root/'exports'
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/(new_id('dataset_p2')+'.zip')
        counts={name:sum(x['split']==name for x in approved) for name in ('train','evaluation')}
        manifest={'schema':'bughunter.p2.dataset.v1','exported_at':now(),
                  'project_id':project_id,'counts':counts,'quarantined':len(quarantine),
                  'holdout_present':counts['evaluation']>0,
                  'training_started':False,'weight_changes':False,
                  'rules':['source-provenance-validation','explicit-human-review','training-consent',
                           'family-and-question-split-isolation','duplicate-pair-removal']}
        try:
            with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for split in ('train','evaluation'):
                    entries=[r for r in approved if r['split']==split]
                    with z.open(split+'.jsonl','w') as out:
                        for r in entries:
                            payload={'messages':[{'role':'user','content':redact_text(r['prompt'][:MAX_TEXT])},
                                                 {'role':'assistant','content':redact_text(r['answer'][:MAX_TEXT])}]}
                            out.write((json.dumps(payload,ensure_ascii=False)+'\n').encode())
                with z.open('provenance.jsonl','w') as out:
                    for r in approved:
                        out.write((json.dumps({'id':r['id'],'split':r['split'],'source':r['source'],
                                               'citation':r['citation'],'origin':r['origin'],
                                               'content_sha256':sha(r['prompt']+'\0'+r['answer'])},ensure_ascii=False)+'\n').encode())
                z.writestr('quarantine.jsonl',''.join(json.dumps(x)+'\n' for x in quarantine))
                z.writestr('manifest.json',json.dumps(manifest,indent=2))
                z.writestr('README.txt','Review privacy, source licenses and held-out evaluation before training.\n'
                           'Files contain examples, not trained model weights. No generated labels were added.\n')
            manifest['zip_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            return {'path':str(target),'manifest':manifest}
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def dispatch(self,method,p):
        options={'dataset.p2.inspect':lambda:self.inspect(p.get('project_id','*')),
                 'dataset.p2.export':lambda:self.export(p.get('project_id','*')),
                 'dataset.p2.consent':lambda:self.consent(p['id'],p['allowed'])}
        if method not in options:raise ValueError('Unknown P2 dataset operation.')
        return options[method]()
