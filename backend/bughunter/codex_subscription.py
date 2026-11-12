"""ChatGPT subscription transport through the installed Codex app-server.

Credentials stay with Codex. No API-key fallback or direct token extraction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time

from .security import redact_text

_ACTIVE = set()
_LOCK = threading.Lock()

# Disable ambient integrations; app tools are supplied explicitly per thread.
CONFIG = {
    'model_provider': 'openai', 'forced_login_method': 'chatgpt',
    'approval_policy': 'never', 'sandbox_mode': 'read-only',
    'web_search': 'disabled', 'mcp_servers': {}, 'project_doc_max_bytes': 0,
    'developer_instructions': '',
    'features.shell_tool': False, 'features.apps': False, 'features.plugins': False,
    'features.codex_hooks': False, 'features.hooks': False,
    'features.multi_agent': False, 'features.multi_agent_v2': False,
    'features.memories': False, 'features.memory_tool': False,
    'features.computer_use': False, 'features.browser_use': False,
    'features.js_repl': False, 'features.code_mode': False,
    'features.image_generation': False, 'skills.include_instructions': False,
    'notify': [],
}


def cancel_all():
    with _LOCK:
        sessions = list(_ACTIVE)
    for session in sessions:
        session.cancel.set()


class CodexSession:
    def __init__(self, stop=None, timeout=30, cancel_check=None):
        self.cancel = stop if stop is not None else threading.Event()
        self.timeout = timeout
        self.events = queue.Queue()
        self.sequence = 0
        self.process = None
        self.workspace = None
        self.cancel_check = cancel_check
        self._reader = None

    def _check_cancelled(self):
        if self.cancel.is_set():
            raise InterruptedError('ChatGPT task stopped.')
        if self.cancel_check:
            self.cancel_check()

    def __enter__(self):
        self._check_cancelled()
        executable = shutil.which('codex')
        if not executable:
            raise ValueError('Install Codex, run codex login, then refresh models.')
        self.workspace = tempfile.TemporaryDirectory(prefix='bughunter-codex-')
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('OPENAI_', 'AZURE_OPENAI_')) or key in {'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'}:
                env.pop(key, None)
        args = [executable, 'app-server']
        for key, value in CONFIG.items():
            # JSON scalar/empty collection values are also valid TOML.
            args += ['-c', key + '=' + json.dumps(value)]
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, cwd=self.workspace.name,
                env=env, start_new_session=True, bufsize=1)
            with _LOCK:
                _ACTIVE.add(self)
            self._reader = threading.Thread(target=self._read, daemon=True)
            self._reader.start()
            self.call('initialize', {'clientInfo': {'name': 'bughunter', 'version': '0.1.0'},
                                     'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.events.put(None)

    def send(self, value):
        self._check_cancelled()
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def receive(self, deadline):
        while True:
            self._check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError('Codex did not respond before the time limit.')
            try:
                event = self.events.get(timeout=.1)
            except queue.Empty:
                continue
            if event is None:
                raise ValueError('Codex exited. Check its installation, sign-in and subscription limits.')
            self._check_cancelled()
            return event

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while True:
            event = self.receive(deadline)
            if event.get('id') == request_id and 'method' not in event:
                if 'error' in event:
                    raise ValueError(redact_text(event['error'].get('message', 'Codex request failed.')))
                return event.get('result', {})
            if 'id' in event and 'method' in event:
                self.send({'id': event['id'], 'error': {'code': -32601, 'message': 'Unsupported request.'}})

    def account(self):
        account = self.call('account/read', {'refreshToken': False}).get('account') or {}
        return {'ready': account.get('type') == 'chatgpt', 'plan': account.get('planType')}

    def models(self):
        rows, cursor = [], None
        for _ in range(10):
            result = self.call('model/list', {'limit': 100, 'cursor': cursor})
            rows.extend({'id': r['model'], 'label': r.get('displayName') or r['model'],
                         'default': bool(r.get('isDefault'))} for r in result.get('data', []))
            cursor = result.get('nextCursor')
            if not cursor:
                return rows
        raise ValueError('Codex model catalog exceeded the page limit.')

    def run(self, model, system, prompt, specs=(), tool_handler=None, log=None, max_calls=20,
            *, images=None, reasoning_effort=None):
        self._check_cancelled()
        # Validated against codex-cli 0.166.0 generated TurnStartParams schema:
        # UserInput.localImage has a path; effort is the turn-level override.
        image_inputs = []
        if images is not None:
            if not isinstance(images, (list, tuple)) or len(images) > 3:
                raise ValueError('Supply at most three local frame images.')
            for image in images:
                self._check_cancelled()
                path = Path(image).expanduser()
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 10 * 1024 * 1024:
                    raise ValueError('Each frame must be a regular file of at most 10 MiB.')
                image_inputs.append({'type': 'localImage', 'path': str(path.resolve(strict=True))})
        if reasoning_effort is not None and reasoning_effort != 'medium':
            raise ValueError('The optional media reasoning effort must be medium.')
        if not self.account()['ready']:
            raise ValueError('Sign in with your ChatGPT account using codex login, then refresh models.')
        # Verify that no inherited MCP connector can be invoked by this thread.
        effective = self.call('config/read', {'includeLayers': False}).get('config', {})
        if effective.get('mcp_servers'):
            raise ValueError('Codex could not isolate the app connection from other MCP servers.')
        params = {'model': model, 'modelProvider': 'openai', 'baseInstructions': system, 'developerInstructions': '',
                  'ephemeral': True, 'approvalPolicy': 'never', 'sandbox': 'read-only',
                  'environments': [], 'selectedCapabilityRoots': [],
                  'dynamicTools': [{'type': 'function', 'name': s['name'],
                                    'description': s['description'], 'inputSchema': s['parameters']}
                                   for s in specs], 'allowProviderModelFallback': False}
        thread = self.call('thread/start', params)['thread']['id']
        # Start without consuming notifications that can precede the RPC response.
        self.sequence += 1
        start_id = self.sequence
        turn_params = {'threadId': thread, 'input': [{'type': 'text', 'text': prompt}] + image_inputs,
                       'environments': []}
        if reasoning_effort is not None:
            turn_params['effort'] = reasoning_effort
        self.send({'id': start_id, 'method': 'turn/start', 'params': turn_params})
        allowed = {s['name'] for s in specs}
        replies, calls, seen_calls = [], 0, set()
        deadline = time.monotonic() + self.timeout
        while True:
            event = self.receive(deadline)
            method, data = event.get('method'), event.get('params', {})
            if event.get('id') == start_id and 'error' in event:
                raise ValueError(redact_text(event['error'].get('message', 'ChatGPT request failed.')))
            if 'id' in event and method:
                if method != 'item/tool/call':
                    self.send({'id': event['id'], 'error': {'code': -32601, 'message': 'Only app tools are supported.'}})
                    continue
                name, call_id = data.get('tool'), data.get('callId')
                if name not in allowed or not tool_handler or call_id in seen_calls:
                    raise ValueError('Codex requested an unavailable or duplicate app tool.')
                calls += 1
                if calls > max_calls:
                    raise ValueError('The task reached its configured tool-call limit.')
                seen_calls.add(call_id)
                self._check_cancelled()
                try:
                    value = tool_handler(name, data.get('arguments', {}))
                    success = True
                except InterruptedError:
                    raise
                except Exception as exc:
                    value, success = {'error': str(exc)}, False
                from .security import redact
                image = value.pop('image_base64', None) if isinstance(value, dict) else None
                content = [{'type': 'inputText', 'text': json.dumps(redact(value), default=str)[:100000]}]
                if image:
                    mime = value.get('media_type', 'image/jpeg')
                    content.append({'type': 'inputImage', 'imageUrl': f'data:{mime};base64,{image}'})
                self.send({'id': event['id'], 'result': {'contentItems': content, 'success': success}})
                if log:
                    log('tool', name)
            elif method == 'item/completed' and data.get('threadId') == thread:
                item = data.get('item', {})
                if item.get('type') == 'agentMessage' and item.get('text'):
                    replies.append(redact_text(item['text']))
                    if log:
                        log('assistant', replies[-1])
            elif method == 'turn/completed' and data.get('threadId') == thread:
                turn = data.get('turn', {})
                if turn.get('status') != 'completed':
                    message = (turn.get('error') or {}).get('message') or 'ChatGPT task interrupted.'
                    raise ValueError(redact_text(message))
                if not replies:
                    raise ValueError('ChatGPT returned no readable reply.')
                return '\n\n'.join(replies)

    def __exit__(self, *_):
        with _LOCK:
            _ACTIVE.discard(self)
        if self.process:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
                except ProcessLookupError:
                    pass
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe:
                    pipe.close()
        if self.workspace:
            self.workspace.cleanup()
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)


def connection_status():
    try:
        with CodexSession() as session:
            status = session.account()
            models = session.models() if status['ready'] else []
            return {**status, 'models': models, 'detail': 'Signed in with ChatGPT.' if status['ready'] else 'Run codex login, then refresh models.'}
    except (ValueError, OSError, TimeoutError) as exc:
        return {'ready': False, 'models': [], 'detail': redact_text(str(exc))}


def complete(messages, system, model):
    prompt = '\n\n'.join(f"{m['role']}: {m['content']}" for m in messages if m['role'] != 'system')
    with CodexSession(timeout=180) as session:
        return session.run(model, system, prompt)
