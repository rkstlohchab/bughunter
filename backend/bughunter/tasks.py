"""One read model for work actually performed by existing integrations.

The hub does not confer privileges or silently execute inferred model commands.
Each task links to an underlying runner and obtains its actual status and events.
"""
from __future__ import annotations

import threading
import re
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit
from .security import redact_text
from .store import new_id, now

KINDS = {'orchestrated', 'coding_agents', 'web_investigation', 'study', 'tor_capture', 'memory_handoff', 'tor_browser', 'memory_session', 'desktop_control', 'cart_lab', 'staging_price', 'website_audit'}
ACTIVE = {'starting', 'running', 'queued', 'awaiting_approval', 'awaiting_retry', 'cancelling'}
FINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'paused'}
EXTERNAL_KINDS = {'memory_session', 'desktop_control', 'tor_browser'}
EVENT_KINDS = {'tool_call', 'tool_result', 'approval_required', 'approval_granted', 'approval_denied', 'blocked', 'notice', 'error', 'connected', 'disconnected', 'navigate', 'open', 'capture', 'close', 'recovered'}
ALLOWED_STATES = {'running', 'awaiting_approval', 'awaiting_retry', 'completed', 'failed', 'cancelled', 'interrupted'}

def _safe_field(value, limit=160):
    # Never retain raw tool arguments, memory bytes, model transcripts or credentials.
    cleaned = redact_text(re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or '')))
    return re.sub(r'https?://[^\s<>"\']+', lambda m: _origin_path(m.group()), cleaned)[:limit]

def _origin_path(value):
    try:
        parts = urlsplit(str(value))
        if parts.scheme not in ('http', 'https') or not parts.hostname: return ''
        return urlunsplit((parts.scheme, parts.hostname, parts.path[:256], '', ''))[:320]
    except ValueError:
        return ''

class TaskService:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._lock = threading.RLock()
        self._cancel_events = {}
        # Reconcile records after a crash. In-flight network capture cannot be
        # resumed, and no unverified operation is reported as completed.
        for t in self.store.list('work_task') if getattr(app, 'primary', True) else []:
            if t.get('kind') in {'tor_capture', 'tor_browser', 'memory_session', 'desktop_control', 'orchestrated', 'cart_lab', 'staging_price', 'website_audit', 'autopilot'} and t.get('status') in {'starting', 'running', 'awaiting_approval', 'awaiting_retry', 'cancelling'}:
                self._update(t['id'], status='interrupted', error='Desktop restarted. Review recorded activity; no tool action has been replayed.')
                self._append(t['id'], 'recovered', 'Previous session interrupted; reopening requires a new connection.')

    def _update(self, id, **fields):
        with self._lock:
            prior = self.store.get('work_task', id)
            if not prior: raise ValueError('Task not found.')
            if 'events' in fields:
                self._archive_events(prior, prior.get('events', []) + fields['events'])
                fields['events'] = fields['events'][-200:]
            return self.store.put('work_task', {**prior, **fields}, id=id)

    @staticmethod
    def _event_key(task_id, event):
        return 'task_event_' + hashlib.sha256((task_id + json.dumps(event, sort_keys=True)).encode()).hexdigest()

    def _archive_events(self, task, events):
        # Keep a small live tail in the task and append immutable activity to
        # separate records so long sessions do not discard earlier receipts.
        stamp = now()
        rows = []
        for event in events:
            key = self._event_key(task['id'], event)
            value = {'id': key, 'task_id': task['id'], 'project_id': task.get('project_id'),
                     'event': event, 'created_at': stamp, 'updated_at': stamp}
            rows.append((key, 'work_task_event', json.dumps(value), stamp, stamp))
        with self.store.connection(immediate=True) as db:
            if task.get('project_id') and db.execute('SELECT 1 FROM settings WHERE key=?',
                    ('deleted_project:' + task['project_id'],)).fetchone():
                raise ValueError('This project was deleted; its activity cannot be updated.')
            db.executemany('INSERT OR IGNORE INTO entities(id,kind,data,created_at,updated_at) VALUES(?,?,?,?,?)', rows)

    def _append(self, id, event, message):
        with self._lock:
            task = self.store.get('work_task', id)
            if not task:return
            self._update(id, events=task.get('events', []) + [{'kind':event, 'text':redact_text(str(message))[:1500], 'time':now()}])

    def _linked(self, task):
        kind, link = task['kind'], task.get('linked_id')
        if not link: return None
        if kind == 'coding_agents': return self.store.get('agent_job', link)
        if kind in ('study', 'web_investigation'): return self.store.get('run', link)
        if kind == 'app_checks' and task.get('linked_kind') == 'p7_run': return self.store.get('p7_run', link)
        return None

    def _events(self, task, linked=None):
        with self.store.connection() as db:
            saved = db.execute("SELECT data FROM entities WHERE kind='work_task_event' AND json_extract(data,'$.task_id')=? ORDER BY rowid", (task['id'],)).fetchall()
        events = [json.loads(r[0])['event'] for r in saved]
        known = {self._event_key(task['id'], e) for e in events}
        events.extend(e for e in task.get('events', []) if self._event_key(task['id'], e) not in known)
        if linked and task['kind'] == 'coding_agents':
            rows = self.store.list('agent_job_event', job_id=linked['id'])
            rows.reverse()
            events.extend({'id':e['id'], 'kind':e.get('kind','output'), 'agent':e.get('agent'),
                           'text':e.get('content',''), 'time':e.get('created_at')} for e in rows)
        elif linked:
            events.extend({'kind':e.get('kind') or e.get('type','event'), 'text':e.get('text',''),
                           'time':e.get('time')} for e in linked.get('events', []))
        return events

    def history(self, id, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 250:
            raise ValueError('Activity requires a nonnegative offset and a limit from 1 to 250.')
        task = self.dispatch('tasks.get', {'id': id})
        # Reload the original record; describe() already includes linked events.
        row = self.store.get('work_task', id) or {**task, 'events': []}
        events = self._events(row, self._linked(row))
        end = offset + limit
        return {'events':events[offset:end], 'total':len(events), 'offset':offset,
                'next_offset':end if end < len(events) else None}

    def describe(self, task, full=False):
        row = {**task}
        linked = self._linked(task)
        if linked:
            state = linked.get('status','unknown')
            row['status'] = {'passed':'completed', 'timed_out':'failed'}.get(state, state)
            row['result_status'] = state
            row['updated_at'] = max(row.get('updated_at') or '', linked.get('updated_at') or '')
            row['error'] = linked.get('error') or row.get('error')
            row['summary'] = linked.get('summary') or linked.get('detail') or row.get('summary', '')
            if task['kind'] == 'coding_agents':
                row['workers'] = linked.get('workers', {})
                if not row['summary']:
                    row['summary'] = '\n\n'.join(f'{name}: {worker["summary"]}' for name, worker in row['workers'].items() if worker.get('summary'))
            elif task['kind'] == 'app_checks':
                row['result'] = {key:linked.get(key) for key in ('suite','status','exit_code','output','detail','results','duration_seconds','finished_at')}
                if row['status'] == 'failed': row['error'] = linked.get('detail') or 'App checks failed. Review the saved output.'
            elif full:
                row['approvals'] = [
                    {'id':a['id'],'status':a['status'],'method':a.get('method','')}
                    for a in self.store.list('action', run_id=linked['id'])
                    if a.get('status') in ('pending_approval','awaiting_approval','approved')][:30]
        elif task.get('linked_id') and task.get('kind') in {'coding_agents', 'study', 'web_investigation', 'app_checks'}:
            row['status'] = 'interrupted'
            row['error'] = 'The linked execution record is unavailable. No completion can be verified.'
        if full:
            events = self._events(task, linked)
            row['events'] = events[-250:]
            row['event_count'] = len(events)
            row['events_truncated'] = len(events) > 250
        if not full:
            row.pop('events',None)
            row.pop('args',None)
        return row

    def dispatch(self, method, params):
        if method == 'tasks.events': return self.history(**params)
        if method == 'tasks.external.start': return self.external_start(**params)
        if method == 'tasks.external.event': return self.external_event(**params)
        if method == 'tasks.external.step': return self.external_step(**params)
        if method == 'tasks.external.finish': return self.external_finish(**params)
        if method == 'tasks.list':
            project = params.get('project_id')
            existing = self.store.list('work_task')
            items = [self.describe(row) for row in existing if not project or row.get('project_id') == project]
            # Import legacy tasks into the view without altering or duplicating data.
            references = {(t.get('kind'), t.get('linked_id')) for t in existing}
            for kind, raw_kind, label in [('coding_agents','agent_job','Coding agents'), ('web_investigation','run','Investigation')]:
                for record in self.store.list(raw_kind):
                    resolved = ('study' if record.get('kind') == 'study' else kind)
                    if (resolved,record['id']) in references: continue
                    if project and record.get('project_id') != project: continue
                    items.append({'id':record['id'],'kind':resolved,'title':record.get('title') or record.get('prompt','')[:100] or label,
                                  'status':record.get('status','unknown'),'project_id':record.get('project_id'),
                                  'linked_id':record['id'],'updated_at':record.get('updated_at'), 'legacy':True})
            return sorted(items,key=lambda item:item.get('updated_at') or '',reverse=True)[:150]
        if method == 'tasks.get':
            id = str(params.get('id',''))
            row = self.store.get('work_task',id)
            if not row:
                for kind, storekind in [('coding_agents','agent_job'),('web_investigation','run')]:
                    record = self.store.get(storekind,id)
                    if record:
                        row={'id':id,'kind':'study' if record.get('kind')=='study' else kind,'linked_id':id,
                             'title':record.get('title') or record.get('prompt','')[:100], 'status':record.get('status'),
                             'events':[], 'project_id':record.get('project_id'), 'legacy':True}
                        break
            if not row:raise ValueError('Task not found.')
            if row.get('kind') == 'orchestrated' and getattr(self.app, 'orchestrator', None):
                row = self.app.orchestrator.get(id)
            return self.describe(row, full=True)
        if method == 'tasks.start':return self.start(**params)
        if method == 'tasks.cancel':return self.cancel(params['id'])
        if method == 'tasks.retry':return self.retry(params['id'])
        raise ValueError('Unknown task operation.')

    def external_start(self, kind, title='', project_id=None, source_id=None):
        """Record one operator-started desktop bridge session; does not execute tools."""
        if kind not in {'memory_session', 'desktop_control'}:
            raise ValueError('Unsupported desktop task reporting kind.')
        if project_id and not self.store.get('project', project_id):
            raise ValueError('Unknown project.')
        with self._lock:
            return self.describe(self.store.put('work_task', {
                'kind': kind, 'title': _safe_field(title or 'Desktop task', 110),
                'status': 'running', 'project_id': project_id, 'source_id': None,
                'source': 'desktop_reported', 'events': [{'kind': 'connected', 'text': 'Desktop-controlled session started after user authorization.', 'time': now()}]
            }))

    def external_event(self, id, kind, name='', status=None):
        """Metadata only: no arguments, payloads or tool output are accepted or stored."""
        with self._lock:
            row = self.store.get('work_task', id)
            if not row or row.get('kind') not in {'memory_session','desktop_control'}: raise ValueError('Desktop task not found.')
            if row.get('status') not in ACTIVE: raise ValueError('Memory session is no longer active.')
            if kind not in EVENT_KINDS: raise ValueError('Unknown event kind.')
            if status is not None and status not in {'running', 'awaiting_approval', 'awaiting_retry'}:
                raise ValueError('Invalid intermediate status.')
            message = {'kind': kind, 'name': _safe_field(name, 96),
                       'text': f'{kind.replace("_", " ")}: {_safe_field(name, 96)}', 'time': now()}
            self._update(id, events=(row.get('events', []) + [message])[-200:],
                         status=status or row['status'])
            return {'id': id, 'recorded': True}

    def external_step(self, id, action, observation_hash, native_confirmed):
        """Save minimal, privacy-preserving receipts from a desktop UI action.

        The frontend reports an actual native command result. The backend does
        not attest that it happened independently: the source is explicitly
        labelled 'native-client reported'. No control text, keystrokes, URL,
        password, window title, screenshot or model prompt is stored.
        """
        allowed={'read_ui','click_button','click_tab','click_menu','select_item',
                 'scroll','focus_field','type_text','key'}
        if action not in allowed or type(native_confirmed) is not bool:
            raise ValueError('Invalid native desktop receipt.')
        if not isinstance(observation_hash,str) or not re.fullmatch(r'[0-9a-f]{64}', observation_hash):
            raise ValueError('Provide an SHA-256 digest, not visible UI contents.')
        with self._lock:
            row=self.store.get('work_task',str(id))
            if not row or row.get('kind')!='desktop_control' or row.get('status') not in ACTIVE:
                raise ValueError('Desktop session is unavailable or no longer running.')
            prior=row.get('desktop_receipts') or []
            if len(prior)>=100: raise ValueError('Desktop receipt limit reached.')
            receipt={'step':len(prior)+1,'action':action,
                     'observation_sha256':observation_hash,
                     'native_confirmed':native_confirmed,
                     'provenance':'native-client reported; not independently attested',
                     'time':now()}
            self._update(str(id),desktop_receipts=prior+[receipt],
                         events=(row.get('events',[])+[{'kind':'tool_result',
                             'text':f'Desktop reported {action}: {"accepted" if native_confirmed else "failed"}; UI hash recorded.',
                             'time':now()}])[-200:])
            return {'recorded':True,'step':receipt['step'],'provenance':receipt['provenance']}

    def external_finish(self, id, status='completed', error=None):
        with self._lock:
            row = self.store.get('work_task', id)
            if not row or row.get('kind') not in {'memory_session','desktop_control'}: raise ValueError('Desktop task not found.')
            if row.get('status') in FINAL: return self.describe(row)
            if status not in {'completed', 'failed', 'cancelled', 'interrupted'}:
                raise ValueError('Invalid terminal status.')
            event = {'kind': 'finished', 'text': f'Desktop reported {status}.', 'time': now()}
            return self.describe(self._update(id, status=status, error=_safe_field(error, 240) if error else None,
                          events=(row.get('events', []) + [event])[-200:]))

    def browser_start(self, source_id):
        source = self.store.get('tor_source', source_id)
        if not source: raise ValueError('Unknown browser source.')
        return self.store.put('work_task', {
            'kind': 'tor_browser', 'title': 'Tor Browser: ' + _safe_field(source.get('title') or 'Saved source', 85),
            'project_id': source.get('project_id'), 'source_id': source_id,
            'status': 'starting', 'source': 'backend_observed',
            'events': [{'kind': 'open', 'text': 'Opening saved research source.', 'time': now()}]
        })

    def browser_event(self, id, method, result=None, error=None):
        with self._lock:
            row = self.store.get('work_task', id)
            if not row or row.get('kind') != 'tor_browser': return
            if row.get('status') in FINAL: return
            entry = {'kind': method.replace('tor.browser.', ''), 'time': now()}
            if error is not None:
                entry['text'] = 'Browser operation failed: ' + _safe_field(error, 240)
                self._update(id, events=(row.get('events', []) + [entry])[-200:],
                             status='failed' if method == 'tor.browser.open' else 'running',
                             error=_safe_field(error, 240))
                return
            entry['text'] = 'Browser operation succeeded.'
            if isinstance(result, dict):
                entry['url'] = _origin_path(result.get('url', ''))
                if method == 'tor.browser.capture':
                    entry['capture_id'] = _safe_field(result.get('id'), 96)
            self._update(id, events=(row.get('events', []) + [entry])[-200:],
                         status='completed' if method == 'tor.browser.close' else 'running', error=None)

    def start(self, kind, title='', project_id=None, **options):
        if kind not in KINDS or kind in EXTERNAL_KINDS or kind == 'orchestrated':raise ValueError('Unsupported task type.')
        if self.store.setting('emergency_stop', False):raise ValueError('All execution is stopped.')
        if project_id and not self.store.get('project',project_id):raise ValueError('Unknown project.')
        if kind == 'coding_agents':
            args={k:options[k] for k in ('prompt','agents','working_dir','mode','chat_id') if k in options}
            args['project_id']=project_id
            linked=self.app.agent_jobs.start(**args)
        elif kind == 'web_investigation':
            # The operator explicitly started a task for an authorized project.
            # Prepare the built-in *local* gateway automatically; policy scope,
            # per-request approvals and request budgets remain enforced downstream.
            if options.get('auto_connect') is True:
                project = self.store.get('project', project_id or '')
                if not project or not project.get('scope') or not str(project.get('authorization') or '').strip() or project.get('workspace_only'):
                    raise ValueError('Choose an authorized website project before starting automatic setup.')
                gateway = self.store.setting('gateway', {})
                import time
                if not (gateway.get('verified_at') and time.time() - gateway['verified_at'] < 300):
                    port = int(self.app.settings.get().get('gateway_port') or 8091)
                    try:
                        self.app.tools.gateway_start(port=port)
                        self.app.tools.gateway_verify_direct()
                    except Exception as exc:
                        raise ValueError('Could not auto-connect the built-in local gateway. Check backend tools and Settings → Optional tools. No external request was started.') from exc
            linked=self.app.runs.start(project_id=project_id,prompt=options['prompt'])
            args={'prompt':options['prompt']}
        elif kind == 'study':
            linked=self.app.runs.start_study(source_id=options['source_id'])
            args={'source_id':options['source_id']}
        elif kind == 'tor_capture':
            source_id = options.get('source_id')
            if not self.store.get('tor_source',source_id):raise ValueError('Choose a saved Tor research source.')
            pages=options.get('max_pages',1)
            depth=options.get('max_depth',0)
            if type(pages) is not int or not 1<=pages<=12 or type(depth) is not int or not 0<=depth<=2:
                raise ValueError('Tor capture limits: 1–12 pages, depth 0–2.')
            args={'source_id':source_id,'max_pages':pages,'max_depth':depth}
            linked=None
        else:
            # External memory is a handoff, not proof that a scan took place.
            note=str(options.get('prompt','')).strip()
            if not note or len(note)>12000:raise ValueError('Describe the memory task.')
            args={'prompt':note}
            linked=None
        row=self.store.put('work_task', {'kind':kind,'title':str(title or options.get('prompt') or kind)[:110],
            'project_id':project_id,'args':args,'linked_id':linked['id'] if linked else None,
            'status':'awaiting_workspace' if kind=='memory_handoff' else ('starting' if kind=='tor_capture' else linked.get('status','starting')),
            'events':[{'kind':'created','text':f'Task registered: {kind}', 'time':now()}]})
        if kind=='tor_capture':
            self._cancel_events[row['id']] = threading.Event()
            threading.Thread(target=self._collect,args=(row['id'],),daemon=True).start()
        return self.describe(row)

    def _collect(self,id):
        with self._lock:
            t=self.store.get('work_task',id)
            if not t:return
            if t.get('status')=='cancelling':
                self._update(id,status='cancelled')
                self._cancel_events.pop(id,None)
                return
            if t.get('status')!='starting':return
            self._update(id,status='running')
        try:
            cancel = self._cancel_events.get(id)
            result=self.app.tor.collect(**t['args'],cancel_check=cancel.is_set if cancel else None)
            with self._lock:
                if result.get('cancelled') or (cancel and cancel.is_set()):
                    self._update(id,status='cancelled',result=result)
                    self._append(id,'cancelled','Capture stopped between page requests; previously captured pages were retained.')
                    return
                self._append(id,'result',f"Captured {len(result['pages'])} pages; {len(result['errors'])} errors. Pages require review before publication.")
                self._update(id,status='completed' if not result['errors'] else 'failed',result=result,
                             error='Some captures failed; see result.' if result['errors'] else None)
        except Exception as exc:
            self._update(id,status='cancelled' if cancel and cancel.is_set() else 'failed',error=redact_text(str(exc))[:500])
            self._append(id,'error',str(exc))
        finally:
            self._cancel_events.pop(id,None)

    def cancel(self,id):
        task=self.dispatch('tasks.get',{'id':id})
        if task['status'] not in ACTIVE and task['status']!='awaiting_workspace':return task
        if task['kind'] == 'orchestrated': return self.app.orchestrator.cancel(id)
        if task['kind'] == 'cart_lab': return self.app.commerce.cancel(id)
        if task['kind'] == 'staging_price': return self.app.staging_price.cancel(id)
        if task['kind'] == 'website_audit': return self.app.website_audit.cancel(id)
        if task['kind'] == 'app_checks':
            self.app.readiness_p7.stop(task['linked_id'], approved=True)
            return self.dispatch('tasks.get', {'id':id})
        if task['kind'] in EXTERNAL_KINDS: raise ValueError('Close the live browser or memory workspace session; this log cannot stop an external process.')
        if task['kind']=='coding_agents': self.app.agent_jobs.cancel(task['linked_id'])
        elif task['kind'] in ('study','web_investigation'):self.app.runs.stop(task['linked_id'])
        elif task['kind']=='tor_capture':
            with self._lock:
                current = self.store.get('work_task', id)
                if not current or current.get('status') not in ACTIVE:
                    return self.dispatch('tasks.get', {'id':id})
                event=self._cancel_events.get(id)
                if event:event.set()
                self._update(id,status='cancelling' if event else 'interrupted')
                self._append(id,'cancelling','Stop requested; the active network request may finish before cancellation.' if event else 'The capture worker is unavailable; no action was replayed.')
        elif task['kind']=='memory_handoff':
            self._update(id,status='cancelled')
        return self.dispatch('tasks.get',{'id':id})

    def retry(self,id):
        task=self.dispatch('tasks.get',{'id':id})
        if task['status'] not in FINAL:raise ValueError('Only finished or interrupted tasks can be retried.')
        if task['kind'] in ('orchestrated','web_investigation','memory_handoff','tor_browser','memory_session', 'desktop_control', 'cart_lab', 'staging_price', 'website_audit', 'app_checks'):
            raise ValueError('Reopen the workspace to review scope and approvals; these actions are not replayed.')
        if task.get('legacy'):
            raise ValueError('Legacy task has no validated replay settings; start a new task.')
        if task['kind']=='coding_agents' and task['args'].get('mode','read-only')!='read-only':
            raise ValueError('File-editing agent runs cannot be replayed automatically.')
        return self.start(kind=task['kind'],title=task['title'],project_id=task.get('project_id'),**task['args'])
