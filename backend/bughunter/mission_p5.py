"""P5 bounded research missions. All actions are derived from existing P4/P1 evidence.

No course, webpage, or model text is executed as a command. The agent can advance
only source-fidelity checks. Bundled fixture labs require a distinct desktop
confirmation. Session state is persisted after each observation.
"""
from __future__ import annotations

from hashlib import sha256
import threading
import json

from .store import new_id, now

ACTIVE_RUN = {'queued', 'running', 'stopping'}
MAX_CITATION_CHECKS = 8
MAX_LAB_RUNS = 3
MAX_EVENTS = 100


def signature(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


class MissionP5:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.plans = app.learning_p4
        self.experiments = app.knowledge.experiments
        self._lock = threading.RLock()

    def _scope(self, project_id):
        return self.plans._validate_project(project_id)

    def _get(self, id, project_id='*'):
        row = self.store.get('study_mission_p5', id)
        project = self._scope(project_id)
        if not row or (project != '*' and row.get('project_id') not in (None, project)):
            raise ValueError('Study mission not found in this project.')
        return row

    @staticmethod
    def _event(row, kind, **fields):
        return [*row.get('events', [])[-MAX_EVENTS+1:], {'at': now(), 'kind': kind, **fields}]

    def _save(self, row, **fields):
        return self.store.put('study_mission_p5', {**row, **fields}, id=row['id'])

    def _source_signature(self, plan):
        """Source IDs + hashes are immutable mission inputs, not a P4 result hash.

        Changes to lab runs and citations are expected and must not look like
        source drift. Deleted sources, or changed source content, do look stale.
        """
        inputs = []
        for p in plan.get('procedures', []):
            source = self.store.get('source', p['source_id'])
            procedure = self.store.get('course_procedure',p['procedure_id'])
            inputs.append([p['procedure_id'], p['source_id'],
                           (source.get('content_hash') or source.get('sha256') or source.get('file_sha256')) if source else None,
                           signature(procedure.get('steps',[])) if procedure else None])
        return signature(sorted(inputs))

    def list(self, project_id='*'):
        project = self._scope(project_id)
        return [{k:v for k,v in x.items() if k not in {'events','results','source_signature'}}
                for x in self.store.list('study_mission_p5')
                if project == '*' or x.get('project_id') in (None,project)][:100]

    def create(self, *, goal=None, plan_id=None, project_id='*'):
        self.store.require_desktop_owner()
        project = self._scope(project_id)
        if (goal is None) == (plan_id is None):
            raise ValueError('Provide a goal or an existing P4 plan ID, not both.')
        if goal is not None:
            plan = self.plans.create(goal, project_id=project)
        else:
            plan = self.plans.get(plan_id, project_id=project)
        if plan['status'] != 'open':
            raise ValueError('The learning investigation must be open.')
        plan = self.plans.refresh(plan['id'], project_id=project)
        row = self.store.put('study_mission_p5', {
            'plan_id': plan['id'], 'project_id': plan['project_id'], 'goal': plan['goal'],
            'status': 'ready', 'stage': 'source_checks', 'created_at': now(),
            'source_signature': self._source_signature(plan),
            'pending_run_id': None, 'pending_procedure_id': None,
            'citation_checks_started': 0, 'labs_started': 0,
            'results': [], 'events': [{'kind': 'created','at':now(),'plan_id':plan['id']}],
            'summary': 'Source-backed mission. No technique is certified or target-verified.'
        }, id=new_id('mission_p5'))
        return self.get(row['id'], project_id=project)

    def get(self, id, project_id='*'):
        row = self._get(id,project_id)
        plan = self.plans.get(row['plan_id'], project_id=project_id)
        return {**row, 'next': self._recommend(row, plan)}

    def _recommend(self, row, plan):
        if row['status']=='needs_review' and row.get('stage')=='failed_experiment':
            return {'type':'failure_review', 'detail':'A prior experiment failed. Inspect its evidence and explicitly retry a source check if appropriate.'}
        if row['status'] in {'stopped','stale'}:
            return {'type':'stopped' if row['status']=='stopped' else 'source_changed',
                    'detail':'Restart or create a new study mission after reviewing the evidence.'}
        if plan['status']=='archived':
            return {'type':'archived_plan','detail':'Restore the investigation before continuing.'}
        if row.get('pending_run_id'):
            return {'type':'wait','run_id':row['pending_run_id'], 'detail':'Inspect the existing experiment result before advancing.'}
        gaps = plan.get('gaps',[])
        if row['citation_checks_started'] < MAX_CITATION_CHECKS:
            for gap in gaps:
                if gap.get('action')=='check_citations' and gap.get('procedure_id'):
                    return {'type':'citation_check','procedure_id':gap['procedure_id'], 'detail':gap['detail']}
        for gap in gaps:
            if gap.get('action')=='review_experiment' and gap.get('procedure_id'):
                proc = gap['procedure_id']
                available = [x['id'] for x in self.experiments.available(proc) if x['id']!='source_fidelity']
                if available:
                    return {'type':'lab_approval','procedure_id':proc,'recipes':available,
                            'detail':'Bundled offline fixture requires explicit desktop confirmation.'}
        for gap in gaps:
            if gap.get('action') not in {'check_citations','review_experiment'}:
                return {'type':'human_review', 'code':gap.get('code'), 'detail':gap['detail']}
        return {'type':'review_complete','detail':'No further supported automatic check. Review limitations and remaining external validity.'}

    def advance(self, id, project_id='*'):
        with self._lock:
            return self._advance_locked(id, project_id)

    def _advance_locked(self, id, project_id='*'):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False):
            raise ValueError('Emergency stop is active.')
        row = self._get(id, project_id)
        if row['status'] in {'stopped','stale'}:
            raise ValueError('This mission is stopped or stale; start another mission.')
        if row['status']=='needs_review' and row.get('stage')=='failed_experiment':
            return self.get(id,project_id)
        plan = self.plans.get(row['plan_id'],project_id)
        if plan['status']!='open':raise ValueError('Restore the P4 investigation first.')
        plan = self.plans.refresh(row['plan_id'],project_id)
        if self._source_signature(plan) != row['source_signature']:
            row = self._save(row,status='stale',stage='source_changed',
                             events=self._event(row,'source_changed'))
            return self.get(id,project_id)
        observed = False
        if row.get('pending_run_id'):
            run = self.experiments.get(row['pending_run_id'])
            if run['status'] in ACTIVE_RUN:
                return self.get(id,project_id)
            if run['procedure_id'] != row['pending_procedure_id']:
                raise ValueError('Experiment reference mismatch.')
            record = {'run_id':run['id'], 'procedure_id':run['procedure_id'],
                      'recipe':run['recipe'],'status':run['status'],
                      'verification_level':run.get('verification_level'),
                      'source_sha256':run.get('source_sha256'),
                      'error':str(run.get('error') or '')[:500],
                      'hypotheses':[str(x)[:300] for x in (run.get('hypotheses') or [])[:5]], 'at':now()}
            if run['status']!='passed':
                row = self._save(row, pending_run_id=None,pending_procedure_id=None,
                                results=[*row['results'][-49:],record], status='needs_review',
                                stage='failed_experiment',
                                events=self._event(row,'experiment_failed',run_id=run['id'],status=run['status']))
                return self.get(id,project_id)
            row = self._save(row,pending_run_id=None,pending_procedure_id=None,
                             results=[*row['results'][-49:],record],status='ready',
                             events=self._event(row,'experiment_observed',run_id=run['id'],status=run['status']))
            observed = True
        # One update per call. If a result was just observed, return the next
        # recommendation without silently starting a second operation.
        if observed:
            self.plans.refresh(row['plan_id'],project_id)
            return self.get(id,project_id)
        plan = self.plans.refresh(row['plan_id'],project_id)
        choice = self._recommend(row, plan)
        if choice['type']=='citation_check':
            proc_id=choice['procedure_id']
            active = next((r for r in self.experiments.list(procedure_id=proc_id,limit=100)
                           if r['recipe']=='source_fidelity' and r['status'] in ACTIVE_RUN),None)
            if active:
                run=active
                started=0
            else:
                run=self.experiments.start(proc_id,'source_fidelity')
                started=1
            self._save(row, pending_run_id=run['id'],pending_procedure_id=proc_id,
                       status='waiting',stage='source_checks',
                       citation_checks_started=row['citation_checks_started']+started,
                       events=self._event(row,'citation_check_started' if started else 'citation_check_attached',
                                          procedure_id=proc_id,run_id=run['id']))
        elif choice['type']=='lab_approval':
            self._save(row,status='approval_required',stage='lab_review',
                       events=self._event(row,'approval_required',procedure_id=choice['procedure_id']))
        else:
            self._save(row,status='needs_review',stage='review',
                       events=self._event(row,'human_review_required',reason=choice['type']))
        return self.get(id,project_id)

    def approve_lab(self,id,procedure_id,recipe,*,confirm=False,project_id='*'):
        with self._lock:
            return self._approve_lab_locked(id,procedure_id,recipe,confirm=confirm,project_id=project_id)

    def _approve_lab_locked(self,id,procedure_id,recipe,*,confirm=False,project_id='*'):
        self.store.require_desktop_owner()
        if confirm is not True:raise ValueError('An explicit desktop confirmation is required.')
        if self.store.setting('emergency_stop',False):raise ValueError('Emergency stop is active.')
        row=self._get(id,project_id)
        if row['status'] not in {'approval_required','ready','needs_review'} or row.get('pending_run_id'):
            raise ValueError('This mission cannot start a lab now.')
        if row['labs_started'] >= MAX_LAB_RUNS:raise ValueError('Mission laboratory budget exhausted.')
        plan=self.plans.refresh(row['plan_id'],project_id)
        if self._source_signature(plan)!=row['source_signature']:
            self._save(row,status='stale',stage='source_changed',events=self._event(row,'source_changed'))
            raise ValueError('Source or procedure changed. Create a new mission.')
        matched=next((p for p in plan['procedures'] if p['procedure_id']==procedure_id),None)
        if not matched or matched['fidelity_status']!='passed':
            raise ValueError('Verify source citations before running a lab.')
        if recipe=='source_fidelity' or recipe not in {r['id'] for r in self.experiments.available(procedure_id)}:
            raise ValueError('Unsupported bundled lab for this procedure.')
        if matched['lab_status']=='lab_reproduced_only':
            raise ValueError('A supported lab has already passed. Inspect its evidence first.')
        run=self.experiments.start(procedure_id,recipe,approve_lab=True)
        self._save(row, pending_run_id=run['id'],pending_procedure_id=procedure_id,
                   status='waiting',stage='approved_lab',labs_started=row['labs_started']+1,
                   events=self._event(row,'operator_approved_lab',recipe=recipe,run_id=run['id']))
        return self.get(id,project_id)

    def stop(self,id,project_id='*'):
        with self._lock:
            return self._stop_locked(id,project_id)

    def _stop_locked(self,id,project_id='*'):
        self.store.require_desktop_owner()
        row=self._get(id,project_id)
        if row['status']=='stopped':return self.get(id,project_id)
        # This session can be sharing a citation run with P4; do not stop an
        # attached run that P4 or another session owns.
        if row.get('pending_run_id'):
            owned=any(e.get('kind') in {'citation_check_started','operator_approved_lab'}
                      and e.get('run_id')==row['pending_run_id'] for e in row.get('events',[]))
            if owned:self.experiments.stop(row['pending_run_id'])
        self._save(row,status='stopped',stage='stopped',
                   events=self._event(row,'operator_stopped'))
        return self.get(id,project_id)

    def retry_source(self,id,*,project_id='*'):
        """Explicit operator retry after inspecting a failed citation check."""
        with self._lock:
            self.store.require_desktop_owner()
            row=self._get(id,project_id)
            if row['status']!='needs_review' or row['stage']!='failed_experiment':
                raise ValueError('There is no failed source check to retry.')
            if not row['results'] or row['results'][-1]['recipe']!='source_fidelity':
                raise ValueError('Only a source-fidelity check can be retried here.')
            if row['citation_checks_started']>=MAX_CITATION_CHECKS:
                raise ValueError('Citation check budget exhausted.')
            if self.store.setting('emergency_stop',False):
                raise ValueError('Emergency stop is active.')
            plan=self.plans.get(row['plan_id'],project_id)
            if plan['status']!='open':raise ValueError('Restore the investigation first.')
            plan=self.plans.refresh(row['plan_id'],project_id)
            if self._source_signature(plan)!=row['source_signature']:
                self._save(row,status='stale',stage='source_changed')
                raise ValueError('The source changed. Create a new mission.')
            proc=row['results'][-1]['procedure_id']
            if proc not in {p['procedure_id'] for p in plan.get('procedures',[])}:
                raise ValueError('The procedure is no longer in the plan.')
            run=self.experiments.start(proc,'source_fidelity')
            self._save(row,status='waiting',stage='source_checks',pending_run_id=run['id'],
                       pending_procedure_id=proc,citation_checks_started=row['citation_checks_started']+1,
                       events=self._event(row,'operator_retried_citation',run_id=run['id']))
            return self.get(id,project_id)

    def export(self,id,project_id='*'):
        """Local evidence manifest. Never calls external tools or exports source bodies."""
        self.store.require_desktop_owner()
        row=self.get(id,project_id)
        plan=self.plans.get(row['plan_id'],project_id)
        report={'format':'bughunter.p5.study_mission.v1','exported_at':now(),
                'id':row['id'],'project_id':row['project_id'],'goal':row['goal'],
                'status':row['status'],'summary':row['summary'],
                'limits':{'citation_checks_max':MAX_CITATION_CHECKS,'lab_runs_max':MAX_LAB_RUNS},
                'counters':{'citation_checks':row['citation_checks_started'],'lab_runs':row['labs_started']},
                'references':[{'source_id':p['source_id'],'procedure_id':p['procedure_id']}
                              for p in plan.get('procedures',[])],
                'results':row.get('results',[]),'audit':row.get('events',[]),
                'notes':['Source fidelity is not mechanism verification.',
                         'Bundled fixture success does not verify another target.',
                         'Unreviewed web research cannot establish evidence.']}
        encoded=(json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()
        output=(self.store.root/'exports'/f"{row['id']}.p5-evidence.json")
        output.write_bytes(encoded)
        output.chmod(0o600)
        return {'path':str(output),'sha256':sha256(encoded).hexdigest(),'size_bytes':len(encoded),
                'status':'local_evidence_export','id':id}

    def dispatch(self,method,p):
        routes={
            'mission.p5.list':lambda:self.list(p.get('project_id','*')),
            'mission.p5.create':lambda:self.create(goal=p.get('goal'),plan_id=p.get('plan_id'),project_id=p.get('project_id','*')),
            'mission.p5.get':lambda:self.get(p['id'],p.get('project_id','*')),
            'mission.p5.advance':lambda:self.advance(p['id'],p.get('project_id','*')),
            'mission.p5.approve_lab':lambda:self.approve_lab(p['id'],p['procedure_id'],p['recipe'],
                                                           confirm=p.get('confirm',False),project_id=p.get('project_id','*')),
            'mission.p5.stop':lambda:self.stop(p['id'],p.get('project_id','*')),
            'mission.p5.retry_source':lambda:self.retry_source(p['id'],project_id=p.get('project_id','*')),
            'mission.p5.export':lambda:self.export(p['id'],p.get('project_id','*')),
        }
        if method not in routes:raise ValueError('Unknown P5 mission operation.')
        return routes[method]()
