"""Local coding-agent task runner and durable activity ledger.

Only two installed command-line clients may be launched. Commands are argument
arrays, never shell strings. Runs are read-only by default and are never given
an application credential. The UI shows what actually ran, not a guessed status.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from .security import redact_text

ALLOWED_AGENTS = ('claude', 'codex')
MAX_PROMPT = 12000
MAX_EVENT = 3000
MAX_EVENTS = 250
MAX_WORKER_EVENTS = 5000
MAX_SUMMARY = 24000
MAX_RUNNING = 3
MAX_JOB_SECONDS = 15 * 60
MAX_OUTPUT_LINE = 64 * 1024
# CLI clients use their own sign-in state. Do not pass unrelated process secrets.
SECRET_ENV = re.compile(r"(?:API[_-]?KEY|(?:^|_)TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|(?:^|_)AUTH(?:_|$)|COOKIE|SESSION)", re.I)


class AgentJobService:
    def __init__(self, store, recover=True):
        self.store = store
        self._lock = threading.RLock()
        self._processes = {}
        # A desktop restart cannot truthfully report previously launched CLI
        # children as live agents; they no longer have a tracked process handle.
        for job in self.store.list('agent_job') if recover else []:
            if job.get('status') in ('starting', 'running', 'cancelling'):
                self.store.put('agent_job', {**job, 'status': 'interrupted',
                    'note': 'Desktop restarted. Confirm the CLI process has stopped before retrying.'})

    def dispatch(self, method, params):
        if method == 'agents.list':
            project_id = params.get('project_id')
            return [self._summary(j) for j in self.store.list('agent_job')
                    if not project_id or j.get('project_id') == project_id][:80]
        if method == 'agents.get':
            job = self._get(params['id'])
            events = self.store.list('agent_job_event', job_id=job['id'])
            return {**job, 'events': list(reversed(events[:MAX_EVENTS])),
                    'event_count':len(events), 'events_truncated':len(events) > MAX_EVENTS}
        if method == 'agents.available':
            return {agent: {'installed': bool(shutil.which(agent)), 'path': shutil.which(agent)}
                    for agent in ALLOWED_AGENTS}
        if method == 'agents.start':
            return self.start(**params)
        if method == 'agents.cancel':
            return self.cancel(params['id'])
        raise ValueError('Unknown agent operation.')

    def _get(self, job_id):
        job = self.store.get('agent_job', str(job_id))
        if not job:
            raise ValueError('Agent task not found.')
        return job

    @staticmethod
    def _summary(job):
        return {k: v for k, v in job.items() if k not in {'prompt'}}

    def start(self, prompt, agents, working_dir='', project_id=None, chat_id=None, mode='read-only'):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
            raise ValueError('Enter a task up to 12,000 characters.')
        if not isinstance(agents, list) or not agents or len(agents) > 2 or any(a not in ALLOWED_AGENTS for a in agents) or len(set(agents)) != len(agents):
            raise ValueError('Select Claude Code, Codex, or both.')
        if mode not in ('read-only', 'workspace-write'):
            raise ValueError('Select read-only or local workspace edits.')
        if mode == 'workspace-write' and not working_dir:
            raise ValueError('Select a local project folder before allowing edits.')
        missing = [a for a in agents if not shutil.which(a)]
        if missing:
            raise ValueError('Install and sign in to the missing CLI first: '+', '.join(missing)+'. Open Agents for setup instructions.')
        if working_dir:
            root = Path(working_dir).expanduser().resolve()
            if not root.is_dir():
                raise ValueError('Working folder does not exist. Select a local folder, or leave blank to run without repository context.')
        else:
            root = self.store.root / 'workspaces' / 'agent-research'
            root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            running = sum(j.get('status') in ('running', 'starting', 'cancelling') for j in self.store.list('agent_job'))
            if running >= MAX_RUNNING:
                raise ValueError('Three agent tasks are already active. Stop or finish one before starting another.')
            job = self.store.put('agent_job', {
                'title': redact_text(prompt.strip().splitlines()[0])[:110],
                'prompt': redact_text(prompt.strip()),
                'agents': agents, 'working_dir': str(root), 'project_id': project_id,
                'chat_id': chat_id, 'status': 'starting', 'mode': mode,
                'workers': {name: {'status':'queued','exit_code':None} for name in agents},
                'note': 'Agent events are recorded. For two-agent edit tasks, the first agent edits, then the second reviews in read-only mode.' if mode == 'workspace-write' else 'Running read-only. Agent command and output events are recorded.'
            })
        threading.Thread(target=self._run_job, args=(job['id'],), daemon=True).start()
        return self._summary(job)

    def _update(self, job_id, **fields):
        with self._lock:
            current = self._get(job_id)
            return self.store.put('agent_job', {**current, **fields})

    def _event(self, job_id, agent, kind, content):
        # Execution has a separate, per-worker output bound. A display page
        # limit must never suppress timeout, cancellation or final-result records.
        self.store.put('agent_job_event', {'job_id': job_id, 'agent': agent,
            'kind': kind, 'content': redact_text(str(content))[:MAX_EVENT]})

    @staticmethod
    def _args(agent, prompt, mode='read-only'):
        if agent == 'claude':
            return ['claude', '-p', '--permission-mode', 'acceptEdits' if mode == 'workspace-write' else 'plan', '--max-turns', '8',
                    '--verbose', '--output-format', 'stream-json', prompt]
        return ['codex', 'exec', '--json', '--sandbox', 'workspace-write' if mode == 'workspace-write' else 'read-only',
                '--skip-git-repo-check', '--ephemeral', prompt]

    def _run_job(self, job_id):
        with self._lock:
            job = self._get(job_id)
            if job['status'] in {'cancelled', 'cancelling'}:
                self._update(job_id, status='cancelled')
                return
            self._update(job_id, status='running')
        if job['mode'] == 'workspace-write' and len(job['agents']) == 2:
            # Avoid two independent assistants writing into the same files at once.
            self._run_worker(job_id, job['agents'][0], 'workspace-write')
            if self._get(job_id)['status'] not in {'cancelled', 'cancelling'}:
                self._run_worker(job_id, job['agents'][1], 'read-only')
        else:
            threads = [threading.Thread(target=self._run_worker, args=(job_id, agent), daemon=True)
                       for agent in job['agents']]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        with self._lock:
            current = self._get(job_id)
            results = current['workers']
            if current['status'] in {'cancelled', 'cancelling'}:
                results = {agent:{**worker, 'status':'cancelled'} if worker['status'] in {'queued','running'} else worker
                           for agent,worker in results.items()}
                self._update(job_id, status='cancelled', workers=results)
                return
            overall = 'completed' if all(r['status'] == 'completed' for r in results.values()) else 'failed'
            self._update(job_id, status=overall)

    def _run_worker(self, job_id, agent, override_mode=None):
        job = self._get(job_id)
        if job['status'] in {'cancelled', 'cancelling'}:
            return
        mode = override_mode or job['mode']
        prompt = job['prompt']
        if override_mode == 'read-only' and job['mode'] == 'workspace-write':
            prompt += '\n\nReview the changes already made in this folder. Do not edit files. Report defects, missing checks and verification.'
        args = self._args(agent, prompt, mode)
        self._event(job_id, agent, 'launch', ' '.join(args[:-1]) + ' [TASK PROMPT]')
        try:
            env = {**os.environ}
            # CLI login state remains available from the user's normal CLI config,
            # but unrelated API keys/tokens in the desktop process do not flow
            # into new agent child processes.
            for key in list(env):
                if SECRET_ENV.search(key) or key in {'NGROK_AUTHTOKEN', 'RUNPOD_API_KEY'}:
                    env.pop(key, None)
            proc = subprocess.Popen(args, cwd=job['working_dir'], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                start_new_session=(os.name != 'nt'))
            with self._lock:
                self._processes[(job_id,agent)] = proc
                current = self._get(job_id)
                if current['status'] in {'cancelled', 'cancelling'}:
                    self._terminate(proc)
                workers = {**current['workers'], agent: {'status':'cancelled' if current['status'] in {'cancelled','cancelling'} else 'running', 'pid':proc.pid,'exit_code':None}}
                self._update(job_id, workers=workers)
            timed_out = threading.Event()
            def stop_overdue():
                if proc.poll() is None:
                    timed_out.set()
                    self._terminate(proc)
            watchdog = threading.Timer(MAX_JOB_SECONDS, stop_overdue)
            watchdog.daemon = True
            watchdog.start()
            count = 0
            summary = ''
            output_limited = False
            try:
                while proc.stdout:
                    line = proc.stdout.readline(MAX_OUTPUT_LINE + 1)
                    if not line or self._get(job_id)['status'] in {'cancelled', 'cancelling'}: break
                    if len(line) > MAX_OUTPUT_LINE:
                        self._event(job_id, agent, 'truncated', 'Oversized CLI output line was truncated.')
                        # Do not try to parse an incomplete JSON object.
                        line = line[:MAX_OUTPUT_LINE]
                    line = line.strip()
                    if not line: continue
                    count += 1
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('CLI event must be an object.')
                        kind = str(event.get('type', 'output'))[:60]
                        if kind == 'result' and isinstance(event.get('result'), str):
                            summary = redact_text(event['result'])[:MAX_SUMMARY]
                        item = event.get('item')
                        if kind == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                            summary = redact_text(item['text'])[:MAX_SUMMARY]
                        # Preserve original CLI event as a compact JSON record: commands,
                        # tool results and errors remain available in the task activity.
                        detail = json.dumps(event,ensure_ascii=False)
                    except ValueError:
                        kind, detail = 'output', line
                    self._event(job_id,agent,kind,detail)
                    if count >= MAX_WORKER_EVENTS:
                        output_limited = True
                        self._event(job_id,agent,'limit',f'Worker stopped at the {MAX_WORKER_EVENTS}-event execution limit. Recorded activity is retained; completion was not verified.')
                        break
            finally:
                watchdog.cancel()
            if output_limited and proc.poll() is None:
                self._terminate(proc)
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                code = proc.wait(timeout=10)
            if timed_out.is_set():
                self._event(job_id, agent, 'timeout', f'Agent exceeded the {MAX_JOB_SECONDS} second limit.')
            with self._lock:
                current = self._get(job_id)
                status = 'cancelled' if current['status'] in {'cancelled','cancelling'} else ('failed' if timed_out.is_set() or output_limited else ('completed' if code == 0 else 'failed'))
                workers = {**current['workers'], agent: {'status':status, 'pid':proc.pid, 'exit_code':code, 'summary':summary}}
                self._update(job_id,workers=workers)
                self._event(job_id,agent,'exit',f'Exited with status {code}')
        except Exception as exc:
            with self._lock:
                current = self._get(job_id)
                workers = {**current['workers'], agent: {'status':'cancelled' if current['status'] in {'cancelled','cancelling'} else 'failed', 'exit_code':None}}
                self._update(job_id, workers=workers)
                self._event(job_id,agent,'error',str(exc))
        finally:
            with self._lock:
                self._processes.pop((job_id,agent),None)

    @staticmethod
    def _terminate(proc):
        if proc.poll() is not None: return
        try:
            if os.name != 'nt': os.killpg(proc.pid, signal.SIGTERM)
            else: proc.terminate()
        except ProcessLookupError:
            pass
        # A stalled CLI can ignore SIGTERM. Reap its private process group.
        if proc.poll() is None:
            timer = threading.Timer(5, lambda: AgentJobService._kill(proc))
            timer.daemon = True
            timer.start()

    @staticmethod
    def _kill(proc):
        if proc.poll() is not None: return
        try:
            if os.name != 'nt': os.killpg(proc.pid, signal.SIGKILL)
            else: proc.kill()
        except ProcessLookupError:
            pass

    def cancel(self, job_id):
        with self._lock:
            job = self._get(job_id)
            if job['status'] not in {'starting','running'}:
                return self._summary(job)
            job = self._update(job_id,status='cancelling',note='Stop requested; waiting for agent processes to exit.')
            self._event(job_id, 'system', 'cancelling', 'Operator requested cancellation. Waiting for active processes to exit.')
            for (jid, _), proc in list(self._processes.items()):
                if jid == job_id: self._terminate(proc)
        return self._summary(job)

    def shutdown(self):
        with self._lock:
            active = list(self._processes.values())
            for proc in active: self._terminate(proc)
        # Reap what we own; agents shouldn't keep editing after the app exits.
        for proc in active:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
