"""Durable, bounded task plans on top of the existing execution/approval services.

Planning is deterministic and never grants a new tool permission. Imported content
and model outputs do not provide executable step definitions. Only read-only
preflight/knowledge steps can run without a second operator action.
"""
from __future__ import annotations

import threading
from .store import now
from .security import redact_text
from .tool_readiness import inventory

MODES = {'research', 'code_review', 'web_review', 'model_evaluation'}
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
LINKED_ACTIVE = {'queued', 'starting', 'running', 'awaiting_approval', 'awaiting_retry', 'cancelling'}


def safe_description(text, limit=900):
    return redact_text(str(text or '').replace('\x00', ' ').strip())[:limit]


class Orchestrator:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._lock = threading.RLock()
        # Crash recovery is conservative. No command is ever replayed on boot.
        for row in self.store.list('work_task') if getattr(app, 'primary', True) else []:
            if row.get('kind') == 'orchestrated' and row.get('status') == 'running':
                self._save(row, status='interrupted', note='Desktop restarted. Review each checkpoint before continuing.')

    def dispatch(self, method, p):
        if method == 'plans.create':
            return self.create(**p)
        if method == 'plans.get':
            return self.get(p['id'])
        if method == 'plans.list':
            project_id = p.get('project_id')
            return [self.get(r['id']) for r in self.store.list('work_task')
                    if r.get('kind') == 'orchestrated' and (not project_id or r.get('project_id') == project_id)][:80]
        if method == 'plans.run':
            return self.run(p['id'])
        if method == 'plans.approve':
            return self.approve(p['id'], p.get('agents'), p.get('working_dir', ''))
        if method == 'plans.cancel':
            return self.cancel(p['id'])
        if method == 'plans.report':
            return self.report(p['id'])
        raise ValueError('Unknown plan operation.')

    def _save(self, row, **fields):
        return self.store.put('work_task', {**row, **fields}, id=row['id'])

    def _event(self, row, kind, message):
        return self._save(row, events=(row.get('events', []) + [{
            'kind': kind, 'text': safe_description(message, 250), 'time': now()
        }])[-100:])

    def _read(self, id):
        row = self.store.get('work_task', str(id))
        if not row or row.get('kind') != 'orchestrated':
            raise ValueError('Plan not found.')
        return row

    def create(self, objective, project_id=None, mode='research', source_ids=None):
        if mode not in MODES:
            raise ValueError('Select a supported workflow.')
        if not isinstance(objective, str) or not 3 <= len(objective.strip()) <= 5000:
            raise ValueError('Describe a task in 3–5,000 characters.')
        if project_id and not self.store.get('project', project_id):
            raise ValueError('Project not found.')
        if self.store.setting('emergency_stop', False):
            raise ValueError('Execution is stopped. Re-enable it before creating work.')
        source_ids = source_ids or []
        if not isinstance(source_ids, list) or len(source_ids) > 6 or any(not isinstance(s, str) for s in source_ids):
            raise ValueError('Select no more than six knowledge resources.')
        # Pin only verified source records in the current project or shared library.
        pinned = []
        for source_id in dict.fromkeys(source_ids):
            source = self.store.get('source', source_id)
            if not source:
                raise ValueError('Selected knowledge resource was not found.')
            if source.get('project_id') and source['project_id'] != project_id:
                raise ValueError('Selected knowledge resource belongs to a different project.')
            if source.get('status') in ('queued', 'failed', 'error') or source.get('chunk_count') == 0:
                raise ValueError('Selected knowledge resource is not indexed yet.')
            pinned.append(source_id)
        steps = [
            {'key':'environment', 'title':'Check available tools', 'status':'pending', 'type':'safe'},
            {'key':'knowledge', 'title':'Find relevant project knowledge', 'status':'pending', 'type':'safe'},
            {'key':'methods', 'title':'Match verified methods', 'status':'pending', 'type':'safe'},
        ]
        if mode == 'code_review':
            steps.append({'key':'agents', 'title':'Run a read-only coding-agent review', 'type':'approval', 'status':'pending', 'task_id':None})
        if mode == 'web_review':
            steps.append({'key':'web', 'title':'Start an authorized web investigation', 'type':'approval', 'status':'pending', 'task_id':None})
        if mode == 'model_evaluation':
            steps.append({'key':'eval', 'title':'Review AI-testing setup and test cases in AI Testing', 'type':'handoff', 'status':'pending'})
        row = self.store.put('work_task', {
            'kind':'orchestrated', 'title': safe_description(objective, 110),
            'objective': objective.strip(), 'mode':mode, 'project_id':project_id,
            'status':'draft', 'steps':steps, 'events':[], 'source':'operator_plan', 'source_ids':pinned,
            'summary':'', 'note':'Nothing has been executed yet.',
        })
        return self.get(row['id'])

    def _linked_update(self, row):
        steps = list(row['steps'])
        changed = False
        for i, s in enumerate(steps):
            if s.get('status') != 'running' or not s.get('task_id'):
                continue
            try:
                linked = self.app.tasks.dispatch('tasks.get', {'id':s['task_id']})
            except ValueError:
                linked = {'status':'interrupted'}
            state = linked['status']
            if state in TERMINAL:
                steps[i] = {**s, 'status':state, 'result_status':state, 'finished_at':now()}
                changed = True
        if changed:
            row = self._save(row, steps=steps)
            if row['status'] in TERMINAL:
                return row
            if any(s['status'] == 'failed' for s in steps):
                row = self._save(row, status='failed', note='Linked task failed; inspect its actual activity before retrying.')
            elif any(s['status'] == 'interrupted' for s in steps):
                row = self._save(row, status='interrupted', note='Linked task was interrupted or its record is unavailable; review saved activity before starting new work.')
            elif all(s['status'] in ('completed','ready') for s in steps):
                row = self._save(row, status='completed', note='Plan finished. Review the cited evidence and linked task records.')
            elif any(s['status'] == 'cancelled' for s in steps):
                row = self._save(row, status='cancelled', note='A linked task was cancelled.')
        return row

    def get(self, id):
        with self._lock:
            row = self._linked_update(self._read(id))
            # Do not expose raw model/task arguments as execution instructions.
            return row

    def run(self, id):
        with self._lock:
            row = self._read(id)
            if row['status'] not in ('draft', 'interrupted'):
                raise ValueError('Only a draft or interrupted plan can run its preflight.')
            if self.store.setting('emergency_stop', False):
                raise ValueError('Execution is stopped.')
            if row['status'] == 'interrupted' and any(s['status'] == 'running' for s in row['steps']):
                raise ValueError('A previous external step was running. Review it; start a fresh plan instead of replaying.')
            row = self._save(row, status='running', note='Running non-invasive preflight steps.')
            steps = list(row['steps'])
            for i, s in enumerate(steps):
                if s['status'] != 'pending':
                    continue
                if s['type'] == 'safe':
                    try:
                        if s['key'] == 'environment':
                            checks = inventory(self.store.setting('tool_locations', {}))
                            result = {'detected': [t['name'] for t in checks if t['installed']],
                                      'needs_setup': [t['name'] for t in checks if not t['installed']]}
                        elif s['key'] == 'knowledge':
                            # Always scope to the active project and shared sources.
                            matches = self.app.knowledge.search_for_project(row['objective'], row.get('project_id'), limit=5)
                            result = {'references': [{
                                'source_id': r.get('source_id'), 'chunk_id': r.get('id') or r.get('chunk_id'),
                                'title':safe_description(r.get('title') or r.get('source_title') or 'Reference', 110)
                            } for r in matches]}
                            # Selected documents are always explicitly referenced, even if
                            # the relevance search ranked them below the top five.
                            present = {r['source_id'] for r in result['references']}
                            for source_id in row.get('source_ids', []):
                                if source_id not in present:
                                    source = self.store.get('source', source_id)
                                    if source and (not source.get('project_id') or source['project_id'] == row.get('project_id')):
                                        result['references'].append({
                                            'source_id': source_id,
                                            'title': safe_description(source.get('title') or source.get('filename') or 'Attached source', 110),
                                            'selected': True,
                                        })
                        elif s['key'] == 'methods':
                            skills = self.app.skills.match(row['objective'], row.get('project_id')) if getattr(self.app, 'skills', None) else []
                            result = {'verified_methods': [{'id':v['id'], 'title':v['title'], 'evidence_id':v['evidence_id']} for v in skills]}
                        else:
                            raise ValueError('Unknown preflight operation.')
                        steps[i] = {**s, 'status':'completed', 'result':result, 'finished_at':now()}
                        row = self._event(self._save(row, steps=steps), 'checkpoint', s['title'] + ' completed.')
                    except Exception as exc:
                        steps[i] = {**s, 'status':'failed', 'error':safe_description(exc, 240)}
                        row = self._save(row, steps=steps, status='failed', note='Preflight failed; inspect the recorded error.')
                        return row
                elif s['type'] == 'handoff':
                    steps[i] = {**s, 'status':'awaiting_workspace'}
                    row = self._save(row, steps=steps, status='awaiting_approval', note='Open the specialist workspace to perform and record the review.')
                    return row
                else:
                    steps[i] = {**s, 'status':'awaiting_approval'}
                    row = self._save(row, steps=steps, status='awaiting_approval', note='Review and explicitly start the external work.')
                    return row
            return self._save(row, status='completed', note='Read-only plan complete. Review sources and checkpoints.')

    def approve(self, id, agents=None, working_dir=''):
        with self._lock:
            row = self._read(id)
            if self.store.setting('emergency_stop', False):
                raise ValueError('Execution is stopped.')
            if row['status'] != 'awaiting_approval':
                raise ValueError('The plan has no pending approval.')
            step = next((s for s in row['steps'] if s['status'] == 'awaiting_approval'), None)
            if not step or step['type'] != 'approval':
                raise ValueError('This step is a workspace handoff, not an executable approval.')
            if step['key'] == 'agents':
                # Run strictly in read-only mode. No plan enables workspace edits.
                task = self.app.tasks.start(kind='coding_agents', title=row['title'], project_id=row.get('project_id'),
                                            prompt=row['objective'], agents=agents, working_dir=working_dir, mode='read-only')
            elif step['key'] == 'web':
                project = self.store.get('project', row.get('project_id') or '')
                if not project or project.get('workspace_only') or not project.get('scope') or not project.get('authorization'):
                    raise ValueError('An authorized project with a defined scope is required before starting web work.')
                task = self.app.tasks.start(kind='web_investigation', title=row['title'], project_id=row['project_id'],
                                            prompt=row['objective'])
            else:
                raise ValueError('Invalid approval step.')
            steps = [{**s, 'status':'running', 'task_id':task['id'], 'started_at':now()} if s is step else s for s in row['steps']]
            row = self._save(row, steps=steps, status='running', note='External task started; inspect its recorded activity in Tasks.')
            return self._event(row, 'delegated', f'Linked recorded {step["key"]} task {task["id"]}.')

    def report(self, id):
        row = self.get(id)
        lines = ['# BugHunterClaudex task report', '',
                 '**Objective:** ' + safe_description(row['objective'], 5000),
                 '**Project:** ' + str(row.get('project_id') or 'Shared / unassigned'),
                 '**Status:** ' + row['status'], '',
                 '## Recorded checkpoints', '']
        for step in row['steps']:
            lines.append('### ' + step['title'])
            lines.append('- Status: ' + step['status'])
            if step.get('task_id'):
                lines.append('- Linked execution record: ' + step['task_id'])
            if step.get('error'):
                lines.append('- Error: ' + safe_description(step['error']))
            result = step.get('result') or {}
            for ref in result.get('references', []):
                lines.append('- Knowledge: ' + safe_description(ref.get('title')) + ' (' + str(ref.get('source_id')) + ')')
            for skill in result.get('verified_methods', []):
                lines.append('- Reviewed method: ' + safe_description(skill.get('title')) + ' (evidence ' + str(skill.get('evidence_id')) + ')')
            if 'detected' in result:
                lines.append('- Detected tools (not connectivity-tested): ' + ', '.join(result['detected']))
                lines.append('- Tools needing setup: ' + ', '.join(result.get('needs_setup', [])))
            lines.append('')
        lines.extend(['## Assessment',
                      'This report describes recorded operations and references only. Detection does not prove connectivity, '
                      'and a completed task does not independently prove a security finding. Review the linked original evidence.', ''])
        return {'filename':'bughunter-task-'+row['id'][-12:]+'.md', 'content':'\n'.join(lines), 'status':row['status']}

    def cancel(self, id):
        with self._lock:
            row = self._read(id)
            if row['status'] in TERMINAL:
                return row
            for step in row['steps']:
                if step.get('status') == 'running' and step.get('task_id'):
                    result = self.app.tasks.cancel(step['task_id'])
                    if result and result.get('status') in LINKED_ACTIVE:
                        return self._event(self._save(row, status='cancelling', note='Waiting for the linked task to acknowledge cancellation.'), 'cancelling', 'Operator requested cancellation.')
            return self._event(self._save(row, status='cancelled', note='Plan cancelled; sent network requests cannot be recalled.'), 'cancelled', 'Operator cancelled the plan.')
