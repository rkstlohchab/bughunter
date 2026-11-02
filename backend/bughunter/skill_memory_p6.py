"""P6 reviewable, evidence-bound skill memory.

A skill card is a curated index over P0 citations and P1 experiment receipts;
not a model-weight update, independent target verification, or an executor.
Remote model access is read-only and handled by ModelTools' privacy boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import threading

from .store import new_id, now

MAX_CARDS = 200
MAX_NOTE = 1000
DEFAULT_RECHECK_DAYS = 30


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def source_digest(source):
    return source.get('content_hash') or source.get('sha256') or source.get('file_sha256')


def _passed(run, recipe, procedure_id, source_id, source_hash):
    return bool(run and run.get('status') == 'passed' and run.get('recipe') == recipe
                and run.get('procedure_id') == procedure_id and run.get('source_id') == source_id
                and source_hash and run.get('source_sha256') == source_hash
                and run.get('verification_level') == ('source_verified_only' if recipe == 'source_fidelity' else 'lab_reproduced_only'))


class SkillMemoryP6:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.knowledge = app.knowledge
        self._lock = threading.RLock()

    def _scope(self, project_id):
        return self.app.learning_p4._validate_project(project_id)

    def _procedure(self, procedure_id, project_id):
        project = self._scope(project_id)
        procedure = self.store.get('course_procedure', procedure_id)
        if not procedure: raise ValueError('Candidate procedure not found.')
        source = self.store.get('source', procedure.get('source_id'))
        if not source: raise ValueError('The original source is missing.')
        if project != '*' and (source.get('project_id') not in (None, project)
                               or procedure.get('project_id') not in (None, project)):
            raise ValueError('Candidate procedure is outside this project.')
        if source.get('tor_research_page_id'):
            page = self.store.get('tor_page', source['tor_research_page_id'])
            if not page or not page.get('reviewed'):
                raise ValueError('The research source must be reviewed before creating a skill.')
        return procedure, source

    def _owned(self, id, project_id):
        project = self._scope(project_id)
        row = self.store.get('skill_memory_p6', id)
        if not row or (project != '*' and row.get('project_id') not in (None, project)):
            raise ValueError('Skill record not found in this project.')
        return row

    def _assess(self, row):
        """Compute live validity; never trust a stored promotion flag."""
        procedure = self.store.get('course_procedure', row['procedure_id'])
        source = self.store.get('source', row['source_id'])
        if not procedure or not source or procedure.get('source_id') != row['source_id']:
            return {'level':'stale', 'reason':'Original procedure or source missing', 'source_valid':False,
                    'recheck_due':True, 'citation_run_id':None, 'lab_run_id':None}
        if source.get('tor_research_page_id'):
            page = self.store.get('tor_page',source['tor_research_page_id'])
            if not page or not page.get('reviewed'):
                return {'level':'stale','reason':'Tor capture is no longer reviewed',
                        'source_valid':False,'recheck_due':True,'citation_run_id':None,'lab_run_id':None}
        original_hash = row.get('source_sha256')
        current_hash = source_digest(source)
        if not original_hash or not current_hash or original_hash != current_hash or digest(procedure.get('steps', [])) != row.get('steps_sha256'):
            return {'level':'stale','reason':'Source or procedural steps changed; review again',
                    'source_valid':False,'recheck_due':True,'citation_run_id':None,'lab_run_id':None}
        runs = self.knowledge.experiments.list(procedure_id=procedure['id'],limit=500)
        # The newest run takes precedence: a later failed recheck must not be
        # masked by an earlier passing receipt.
        newest_citation = next((r for r in runs if r.get('recipe') == 'source_fidelity'),None)
        valid_citation = newest_citation if _passed(newest_citation,'source_fidelity',procedure['id'],source['id'],current_hash) else None
        available = {r['id'] for r in self.knowledge.experiments.available(procedure['id'])}
        latest_by_recipe = {}
        for run in runs:
            name = run.get('recipe')
            if name in available and name != 'source_fidelity' and name not in latest_by_recipe:
                latest_by_recipe[name] = run
        valid_labs = [r for r in latest_by_recipe.values() if _passed(r,r['recipe'],procedure['id'],source['id'],current_hash)]
        last_lab = valid_labs[0] if valid_labs else None
        level = 'fixture_reproduced' if valid_citation and last_lab else 'source_checked' if valid_citation else 'candidate'
        expires = row.get('review_due_at')
        try: due = bool(expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc))
        except (TypeError,ValueError): due = True
        if not row.get('reviewed_at'): due = True
        return {'level':level,'reason':('Review is due' if due else 'Evidence bound to original source and fixture') if valid_citation else 'No passing, current source-fidelity check',
                'source_valid':bool(valid_citation),'recheck_due':due,
                'citation_run_id':valid_citation['id'] if valid_citation else None,
                'lab_run_id':last_lab['id'] if last_lab else None}

    def _public(self, row):
        assessment = self._assess(row)
        procedure = self.store.get('course_procedure',row['procedure_id']) or {}
        steps = [{'locator':s.get('locator'), 'chunk_id':s.get('chunk_id'),
                  'quote':str(s.get('quote') or '')[:320]}
                 for s in procedure.get('steps',[])[:20]] if assessment['level'] != 'stale' else []
        return {**row, 'title':procedure.get('title',row.get('title','Skill')),
                'assessment':assessment, 'steps':steps,
                'claim_scope':'source quotation and bundled fixture only; never a guarantee about external targets or model weights'}

    def list(self, project_id='*', query='', limit=50):
        project = self._scope(project_id)
        term = str(query or '').casefold().strip()[:120]
        result = []
        for row in self.store.list('skill_memory_p6'):
            if project != '*' and row.get('project_id') not in (None,project):continue
            if term and term not in (row.get('title','')+' '+row.get('summary','')).casefold():continue
            result.append(self._public(row))
            if len(result)>=max(1,min(MAX_CARDS,int(limit))):break
        return result

    def get(self, id, project_id='*'):
        return self._public(self._owned(id,project_id))

    def create(self, procedure_id, project_id='*'):
        self.store.require_desktop_owner()
        with self._lock:
            procedure,source=self._procedure(procedure_id,project_id)
            project=self._scope(project_id)
            for row in self.store.list('skill_memory_p6'):
                if row['procedure_id']==procedure_id and row.get('project_id')==project:
                    return self.get(row['id'],project)
            if not source_digest(source):raise ValueError('Source must have a content checksum before review.')
            card=self.store.put('skill_memory_p6',{
                'project_id':project,'procedure_id':procedure_id,'source_id':source['id'],
                'source_sha256':source_digest(source),'steps_sha256':digest(procedure.get('steps',[])),
                'title':procedure.get('title','Skill'), 'summary':'', 'reviewed_at':None,
                'review_due_at':None,'review_note':'','review_history':[],
                'created_at':now()},id=new_id('skill_p6'))
            return self.get(card['id'],project)

    def review(self,id,*,confirm=False,summary='',note='',recheck_days=DEFAULT_RECHECK_DAYS,project_id='*'):
        self.store.require_desktop_owner()
        if confirm is not True:raise ValueError('An explicit desktop review confirmation is required.')
        summary=str(summary or '').strip()
        if not 8<=len(summary)<=MAX_NOTE:raise ValueError('Write an 8–1000 character summary.')
        note=str(note or '').strip()[:MAX_NOTE]
        if not isinstance(recheck_days,int) or isinstance(recheck_days,bool) or not 1<=recheck_days<=90:
            raise ValueError('Recheck interval must be 1–90 days.')
        with self._lock:
            row=self._owned(id,project_id)
            assessment=self._assess(row)
            if assessment['level'] not in {'source_checked','fixture_reproduced'}:
                raise ValueError('A current passing citation check is required before review.')
            if row.get('reviewed_at') and assessment['recheck_due'] and row.get('review_history'):
                last = row['review_history'][-1]
                if last.get('citation_run_id') == assessment['citation_run_id']:
                    raise ValueError('Run a new source check before renewing an overdue review.')
            stamp=now()
            due=(datetime.now(timezone.utc)+timedelta(days=recheck_days)).isoformat()
            history=[*row.get('review_history',[])[-29:],{
                'at':stamp,'evidence_level':assessment['level'],
                'citation_run_id':assessment['citation_run_id'],'lab_run_id':assessment['lab_run_id'],
                'summary_sha256':sha256(summary.encode()).hexdigest()}]
            row=self.store.put('skill_memory_p6',{**row,'summary':summary,'review_note':note,
                'reviewed_at':stamp,'review_due_at':due,'review_history':history},id=row['id'])
            return self._public(row)

    def recheck(self,id,project_id='*'):
        """One explicit source-only recheck; never runs a fixture or copied command."""
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False):raise ValueError('Emergency stop is active.')
        with self._lock:
            row=self._owned(id,project_id)
            if self._assess(row)['level']=='stale':
                raise ValueError('The source changed; create a new skill review from the new procedure.')
            for run in self.knowledge.experiments.list(procedure_id=row['procedure_id'],limit=500):
                if run.get('recipe')=='source_fidelity' and run.get('status') in {'queued','running','stopping'}:
                    return {'run_id':run['id'],'status':'already_running','skill_id':row['id']}
            run=self.knowledge.experiments.start(row['procedure_id'],'source_fidelity')
            return {'run_id':run['id'],'status':'started','skill_id':row['id']}

    def export(self,project_id='*'):
        self.store.require_desktop_owner()
        project=self._scope(project_id)
        rows=self.list(project_id=project,limit=MAX_CARDS)
        # Metadata-only export. Private original quotations stay in the local store.
        export={'format':'bughunter.p6.skill_memory.v1','exported_at':now(),
                'project_id':project,'skills':[{k:v for k,v in row.items() if k in {
                    'id','summary','project_id','procedure_id','source_id','source_sha256',
                    'reviewed_at','review_due_at','assessment','claim_scope'}} for row in rows]}
        encoded=(json.dumps(export,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        filename='p6-skills-'+sha256(str(project).encode()).hexdigest()[:12]+'.json'
        output=self.store.root/'exports'/filename
        output.write_bytes(encoded)
        output.chmod(0o600)
        return {'path':str(output),'size_bytes':len(encoded),'sha256':sha256(encoded).hexdigest()}

    def dispatch(self,method,p):
        routes={
            'skill.p6.list':lambda:self.list(project_id=p.get('project_id','*'),query=p.get('query',''),limit=p.get('limit',50)),
            'skill.p6.get':lambda:self.get(p['id'],project_id=p.get('project_id','*')),
            'skill.p6.create':lambda:self.create(p['procedure_id'],project_id=p.get('project_id','*')),
            'skill.p6.review':lambda:self.review(p['id'],confirm=p.get('confirm',False),summary=p.get('summary',''),
                note=p.get('note',''),recheck_days=p.get('recheck_days',30),project_id=p.get('project_id','*')),
            'skill.p6.recheck':lambda:self.recheck(p['id'],project_id=p.get('project_id','*')),
            'skill.p6.export':lambda:self.export(project_id=p.get('project_id','*')),
        }
        if method not in routes:raise ValueError('Unknown P6 skill operation.')
        return routes[method]()
