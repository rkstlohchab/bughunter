"""Select a verified local Ollama installation without changing factory defaults.

Run with the backend virtualenv. --check-only leaves application settings intact.
--smoke performs two metadata-only inference turns in a temporary workspace.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from bughunter.model_tools import ModelTools, TOOL_SPECS
from bughunter.providers import ProviderSettings, fallback_tool_protocol, parse_fallback_call, prompt_text
from bughunter.store import Store, now

MODEL = 'zfujicute/OmniCoder-Qwen3.5-9B-Claude-4.6-Opus-Uncensored-v2-GGUF'
BASE = 'http://127.0.0.1:11434'
COMPATIBLE_MODEL = 'bughunter-omnicoder:9b'


def inspect_model(client, model):
    tags = client.get(BASE + '/api/tags'); tags.raise_for_status()
    canonical = model if ':' in model else model + ':latest'
    installed = next((m for m in tags.json().get('models', []) if m.get('name') == canonical), None)
    if not installed:
        raise ValueError('The exact model tag is not installed. Pull it in Ollama first.')
    response = client.post(BASE + '/api/show', json={'model': model}); response.raise_for_status()
    info = response.json()
    if info.get('remote_host') or info.get('remote_model') or 'cloud' in canonical.lower():
        raise ValueError('Choose a local model; this setup does not enable cloud inference.')
    return {'model': canonical, 'digest': installed.get('digest'), 'size_bytes': installed.get('size'),
            'capabilities': info.get('capabilities', []), 'details': info.get('details', {}),
            'parameters': info.get('parameters', '')}


def prepare_model(client):
    """Reuse the requested GGUF weights with Ollama's Qwen 3.5 renderer/parser."""
    inspect_model(client, MODEL)
    response = client.post(BASE + '/api/create', json={
        'model': COMPATIBLE_MODEL, 'from': MODEL,
        'renderer': 'qwen3.5', 'parser': 'qwen3.5', 'stream': False,
    })
    response.raise_for_status()
    if response.json().get('error'):
        raise ValueError(str(response.json()['error'])[:1000])
    return COMPATIBLE_MODEL


def smoke(client, model, native):
    """Only get_project is exposed and dispatched; no target or OS tool can run."""
    spec = next(s for s in TOOL_SPECS if s['name'] == 'get_project')
    messages = [{'role': 'system', 'content': prompt_text() + '\n' + (
        'Only get_project is available for this connection check.' if native else fallback_tool_protocol([spec]))},
        {'role': 'user', 'content': 'Call get_project exactly once to read this temporary local project. Then report its name exactly. Do not invent its name or call other tools.'}]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='bughunter-model-check-') as directory:
        store = Store(directory)
        marker = 'Local-check-' + secrets.token_hex(6)
        project = store.put('project', {'name': marker, 'scope': [], 'authorization': 'Metadata-only connection check'})
        run = store.put('run', {'project_id': project['id'], 'status': 'running'})
        dispatcher = ModelTools(SimpleNamespace(store=store), run['id'])
        def infer():
            payload = {'model': model, 'messages': messages, 'stream': False, 'think': False,
                       'options': {'num_ctx': 8192, 'num_predict': 256, 'temperature': 0}, 'keep_alive': 0}
            if native:
                payload['tools'] = [{'type': 'function', 'function': spec}]
            response = client.post(BASE + '/api/chat', json=payload)
            data = response.json()
            if data.get('error'): raise ValueError(str(data['error'])[:1000])
            response.raise_for_status()
            return data['message']
        first = infer()
        if native:
            calls = first.get('tool_calls', [])
            if len(calls) != 1:
                raise ValueError('The model did not return exactly one native tool call.')
            call = calls[0]['function']
            name, arguments = call.get('name'), call.get('arguments')
        else:
            call = parse_fallback_call(first.get('content', '')) or {}
            name, arguments = call.get('tool'), call.get('arguments')
        if name != 'get_project' or arguments != {}:
            raise ValueError('The model did not request the allowed metadata tool with empty arguments.')
        result = dispatcher.call(name, arguments)
        messages.append(first)
        messages.append({'role': 'tool' if native else 'user', 'content': json.dumps(result),
                         **({'tool_name': name} if native else {})})
        final = infer()
        if final.get('tool_calls') or marker not in final.get('content', ''):
            raise ValueError('The model did not ground its final answer in the returned project name.')
    return {'status': 'passed', 'protocol': 'native' if native else 'prompt_json',
            'tool': 'get_project', 'round_trips': 2, 'seconds': round(time.monotonic()-started, 2),
            'target_requests': 0, 'limits': 'One metadata tool only; full investigations and visual reasoning are not validated.'}


def configure(store, model):
    for kind in ('run', 'work_task', 'agent_job'):
        if any(r.get('status') in {'running', 'starting', 'queued', 'awaiting_approval'} for r in store.list(kind)):
            raise ValueError('Finish or stop active work before changing its model connection.')
    keys = ('settings', 'capability_toggles', 'model_routes')
    saved = {key: store.setting(key, {}) for key in keys}
    backup = store.root / 'exports' / ('model-settings-' + secrets.token_hex(6) + '.json')
    with os.fdopen(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as file:
        json.dump({'created_at': now(), **saved}, file, indent=2)
    ProviderSettings(store).save({'provider': 'ollama', 'model': model, 'api_base_url': BASE,
                                  'local_only': True, 'use_local_embeddings': False})
    store.set_setting('capability_toggles', {**saved['capability_toggles'],
        'provider_fallback_chain': [], 'paid_fallback': False, 'cloud_transcription': False})
    store.set_setting('model_routes', {**saved['model_routes'], 'enabled': False})
    store.event('settings.local_model_selected', {'provider': 'ollama', 'model': model, 'local_only': True})
    return {'provider': 'ollama', 'model': model, 'endpoint': BASE, 'local_only': True,
            'fallback_enabled': False, 'settings_backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--prepare', action='store_true', help='Create bughunter-omnicoder:9b from the requested weights using the built-in Qwen renderer/parser.')
    args = parser.parse_args()
    with httpx.Client(timeout=httpx.Timeout(240, connect=5), trust_env=False, follow_redirects=False) as client:
        if args.prepare:
            if args.model != MODEL:
                raise ValueError('--prepare applies only to the default requested model.')
            args.model = prepare_model(client)
        info = inspect_model(client, args.model)
        print(json.dumps({'installed': info}), flush=True)
        if args.smoke:
            print(json.dumps({'smoke': smoke(client, args.model, 'tools' in info['capabilities'])}), flush=True)
        if not args.check_only:
            print(json.dumps({'configured': configure(Store(args.data_dir), args.model)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, httpx.HTTPError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
