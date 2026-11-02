"""P4: evidence-driven investigations and gap tracking.

A study plan is an index of *observed* references and outstanding questions,
never evidence that a real-world technique is verified. Reconciliation is
read-only; citation checks use P1's fixed, non-executing source-fidelity recipe.
"""
from __future__ import annotations

import hashlib
import re
from .store import new_id, now

MAX_RESULTS = 8
MAX_PLANS = 100
STOP_WORDS = frozenset('the and for with into from how what when why does that this are can use using make build find learn about your you all have has get take will its should then'.split())


def tokens(text):
    return set(t for t in re.findall(r'[\w-]{3,}', str(text).casefold()) if t not in STOP_WORDS)


def _fingerprint(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class LearningP4:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.knowledge = app.knowledge

    def _validate_project(self, project_id):
        if project_id not in ('*', None) and not self.store.get('project', project_id):
            raise ValueError('Unknown project.')
        return project_id or '*'

    def _owned(self, row, project_id):
        if not row or (project_id != '*' and row.get('project_id') not in (None, project_id)):
            raise ValueError('Learning investigation not found in this project.')
        return row

    def list(self, project_id='*'):
        project_id = self._validate_project(project_id)
        return [{k:v for k,v in p.items() if k not in {'evidence','gaps','history'}}
                for p in self.store.list('learning_plan_p4')
                if project_id == '*' or p.get('project_id') in (None,project_id)][:MAX_PLANS]

    def _get(self, id, project_id='*'):
        return self._owned(self.store.get('learning_plan_p4', id), self._validate_project(project_id))

    def _snapshot(self, goal, project_id, specific_procedure=None):
        goal_words = tokens(goal)
        # KnowledgeService enforces source/project scoping at retrieval time.
        hits = self.knowledge.search(goal, limit=MAX_RESULTS, project_id=project_id)
        evidence = []
        for h in hits:
            source = self.store.get('source', h['source_id'])
            if not source: continue
            # Don't include unreviewed Tor captures or private captures in plans.
            if source.get('tor_research_page_id'):
                page = self.store.get('tor_page',source['tor_research_page_id'])
                if not page or not page.get('reviewed'): continue
            evidence.append({'source_id':h['source_id'], 'chunk_id':h['id'],
                             'locator':h['locator'], 'title':h.get('title','Source'),
                             'excerpt':str(h.get('text',''))[:500], 'kind':'retrieved_excerpt',
                             'source_sha256':source.get('sha256') or source.get('file_sha256')})
        procedures=[]
        missing_specific=False
        if specific_procedure:
            p=self.store.get('course_procedure', specific_procedure)
            if p is None:
                missing_specific=True
                candidates=[]
            else:
                source=self.store.get('source',p['source_id'])
                if not source or (project_id != '*' and source.get('project_id') not in (None,project_id)):
                    raise ValueError('Procedure does not belong to this project.')
                candidates=[p]
        else:
            candidates=self.knowledge.procedures.list(project_id=project_id,limit=150)
        for p in candidates:
            source=self.store.get('source',p['source_id'])
            if not source:continue
            if source.get('tor_research_page_id'):
                page=self.store.get('tor_page',source['tor_research_page_id'])
                if not page or not page.get('reviewed'):continue
            overlap=len(goal_words & tokens(' '.join([p.get('title','')]+[str(s.get('quote','')) for s in p.get('steps',[])])))
            if not specific_procedure and overlap < (2 if len(goal_words)>2 else 1):continue
            procedures.append((overlap,p))
        procedures.sort(key=lambda pair:(-pair[0],pair[1]['id']))
        records=[]
        for _,p in procedures[:5]:
            runs=self.knowledge.experiments.list(procedure_id=p['id'],limit=100)
            fidelity=[r for r in runs if r.get('recipe')=='source_fidelity']
            last_fidelity=fidelity[0] if fidelity else None
            # Experiments without target-specific corroboration remain lab-only.
            lab=[r for r in runs if r.get('recipe')!='source_fidelity']
            passed_lab=next((r for r in lab if r.get('status')=='passed' and r.get('verification_level')=='lab_reproduced_only'),None)
            recipes=self.knowledge.experiments.available(p['id'])
            records.append({'procedure_id':p['id'],'source_id':p['source_id'],
                            'title':p.get('title','Procedure'), 'step_count':len(p.get('steps',[])),
                            'cited_steps':sum(bool(s.get('chunk_id') and s.get('locator') and s.get('quote')) for s in p.get('steps',[])),
                            'fidelity_run_id':last_fidelity['id'] if last_fidelity else None,
                            'fidelity_status':last_fidelity['status'] if last_fidelity else 'not_tested',
                            'lab_run_id':passed_lab['id'] if passed_lab else None,
                            'lab_status':'lab_reproduced_only' if passed_lab else 'not_reproduced',
                            'has_bundled_lab':any(r['id']!='source_fidelity' for r in recipes),
                            'status':'candidate_not_target_verified'})
        gaps=[]
        if missing_specific:
            gaps.append({'code':'procedure_missing','priority':1,
                         'detail':'The original candidate procedure was deleted or rebuilt. Select a new evidence-backed candidate.',
                         'action':'review_procedure'})
        # Use only title/URL metadata for leads in the human review inbox;
        # unreviewed page content never enters a P4 plan or model instructions.
        for discovery in self.store.list('research_discovery')[:100]:
            if discovery.get('status')!='unreviewed':continue
            if project_id != '*' and discovery.get('project_id') not in (None,project_id):continue
            if not (goal_words & tokens(discovery.get('title',''))):continue
            gaps.append({'code':'unreviewed_research_lead','priority':3,
                         'discovery_id':discovery['id'],
                         'detail':'A matching research-source title is in the review inbox; its content remains untrusted.',
                         'action':'review_research'})
            if sum(g['code']=='unreviewed_research_lead' for g in gaps)>=3:break
        if not evidence and not records:
            gaps.append({'code':'missing_sources','priority':1,'detail':'No relevant indexed and reviewable evidence found. Import a lesson or review research first.', 'action':'import_or_review'})
        for p in records:
            if not p['step_count'] or p['cited_steps']!=p['step_count']:
                gaps.append({'code':'incomplete_citations','priority':1,'procedure_id':p['procedure_id'],
                             'detail':'One or more extracted steps lack an exact source locator.', 'action':'review_procedure'})
            if p['fidelity_status']!='passed':
                gaps.append({'code':'citation_not_verified','priority':1,'procedure_id':p['procedure_id'],
                             'detail':'Course quotations have not passed a current source-fidelity check.', 'action':'check_citations'})
            if p['has_bundled_lab'] and p['lab_status']!='lab_reproduced_only':
                gaps.append({'code':'mechanism_not_reproduced','priority':2,'procedure_id':p['procedure_id'],
                             'detail':'A bundled reproduction exists but has not passed. Approval required.', 'action':'review_experiment'})
            if not p['has_bundled_lab']:
                gaps.append({'code':'no_supported_lab','priority':3,'procedure_id':p['procedure_id'],
                             'detail':'No supported fixture reproduces this procedure. A new reviewed fixture is needed.', 'action':'design_fixture'})
        if evidence and not records:
            gaps.append({'code':'no_procedural_evidence','priority':2,
                         'detail':'Relevant passages exist, but no matching evidence-backed procedure has been extracted.', 'action':'review_sources'})
        if records:
            gaps.append({'code':'target_specific_verification_missing','priority':4,
                         'detail':'Lab evidence and citation checks cannot establish real-world target-specific validity.',
                         'action':'review_external_evidence'})
        gaps.sort(key=lambda g:(g['priority'],g['code'],g.get('procedure_id','')))
        return evidence,records,gaps

    def create(self, goal, project_id='*', procedure_id=None):
        self.store.require_desktop_owner()
        if not isinstance(goal,str) or not 6 <= len(goal.strip()) <= 500:
            raise ValueError('Describe the learning goal in 6–500 characters.')
        project_id=self._validate_project(project_id)
        evidence,procedures,gaps=self._snapshot(goal.strip(),project_id,procedure_id)
        plan=self.store.put('learning_plan_p4', {
            'project_id':project_id,'goal':goal.strip(),'procedure_id':procedure_id,
            'status':'open','evidence':evidence,'procedures':procedures,'gaps':gaps,
            'evidence_hash':_fingerprint(repr((evidence,procedures,gaps))),
            'checked_at':now(),'history':[{'event':'created','at':now(), 'evidence_count':len(evidence),'gap_count':len(gaps)}],
            'summary':'Evidence-linked investigation. Not a certification or trained model.'},id=new_id('learning_p4'))
        return plan

    def get(self, id, project_id='*'):
        return self._get(id,project_id)

    def refresh(self, id, project_id='*'):
        self.store.require_desktop_owner()
        plan=self._get(id,project_id)
        if plan['status']=='archived':raise ValueError('Restore the investigation before refreshing.')
        evidence,procedures,gaps=self._snapshot(plan['goal'],plan['project_id'],plan.get('procedure_id'))
        hash=_fingerprint(repr((evidence,procedures,gaps)))
        history=plan.get('history',[])
        if hash!=plan.get('evidence_hash'):
            history=[*history[-49:],{'event':'evidence_changed','at':now(), 'evidence_count':len(evidence),'gap_count':len(gaps)}]
        return self.store.put('learning_plan_p4',{**plan,'evidence':evidence,'procedures':procedures,
                'gaps':gaps,'checked_at':now(),'evidence_hash':hash,'history':history},id=id)

    def check_citations(self, id, procedure_id, project_id='*'):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False):raise ValueError('Emergency stop is active.')
        plan=self._get(id,project_id)
        if plan['status']!='open':raise ValueError('Restore this investigation first.')
        if procedure_id not in {p['procedure_id'] for p in plan['procedures']}:
            raise ValueError('Procedure is not part of this investigation.')
        # Read-only, fixed P1 source-fidelity recipe. A lab requires a separate
        # user action in the existing P1 UI and cannot run here.
        result=self.knowledge.experiments.start(procedure_id,'source_fidelity')
        history=[*plan.get('history',[])[-49:],{'event':'citation_check_started','at':now(),
                                                     'procedure_id':procedure_id,'run_id':result['id']}]
        self.store.put('learning_plan_p4',{**plan,'history':history},id=id)
        return {'experiment_id':result['id'],'status':result['status'],
                'note':'Checks exact citations only. It does not run commands from the lesson.'}

    def advance(self,id,project_id='*'):
        """Run at most one *read-only* P1 citation check per explicit call.

        This is deliberately neither an open-ended planning loop nor a license
        for the model to execute copied course instructions or external tests.
        """
        self.store.require_desktop_owner()
        plan=self.refresh(id,project_id)
        if plan['status'] != 'open':
            raise ValueError('Archived investigation cannot advance.')
        for gap in plan['gaps']:
            if gap['action']!='check_citations':continue
            proc_id=gap['procedure_id']
            prior=self.knowledge.experiments.list(procedure_id=proc_id,limit=100)
            active=next((r for r in prior if r['recipe']=='source_fidelity'
                         and r['status'] in {'queued','running','stopping'}),None)
            if active:
                return {'plan_id':id,'action':'citation_check_running',
                        'experiment_id':active['id'],'status':active['status']}
            result=self.check_citations(id,proc_id,project_id)
            return {'plan_id':id,'action':'citation_check_started',**result}
        next_gap=next(iter(plan['gaps']),None)
        return {'plan_id':id,'action':'human_review_required' if next_gap else 'no_next_action',
                'next_gap':next_gap,
                'note':'Only verified source-citation checks run automatically. New research and lab execution require separate operator actions.'}

    def archive(self,id,archived=True,project_id='*'):
        self.store.require_desktop_owner()
        if type(archived) is not bool:raise ValueError('archived must be boolean.')
        plan=self._get(id,project_id)
        return self.store.put('learning_plan_p4',{**plan,'status':'archived' if archived else 'open'},id=id)

    def dispatch(self,method,p):
        routes={
            'learning.p4.list':lambda:self.list(p.get('project_id','*')),
            'learning.p4.create':lambda:self.create(p['goal'],p.get('project_id','*'),p.get('procedure_id')),
            'learning.p4.get':lambda:self.get(p['id'],p.get('project_id','*')),
            'learning.p4.refresh':lambda:self.refresh(p['id'],p.get('project_id','*')),
            'learning.p4.check_citations':lambda:self.check_citations(p['id'],p['procedure_id'],p.get('project_id','*')),
            'learning.p4.advance':lambda:self.advance(p['id'],p.get('project_id','*')),
            'learning.p4.archive':lambda:self.archive(p['id'],p.get('archived',True),p.get('project_id','*')),
        }
        if method not in routes:raise ValueError('Unknown P4 learning operation.')
        return routes[method]()
