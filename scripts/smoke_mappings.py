"""Cited research -> native owned-value scan -> persisted evidence, in a temporary store.

Only this test process's allocated buffer is scanned. A test harness delivers the
native readback receipt; the Tauri receipt writer is covered by Rust tests.
Default inference is a deterministic fixture. --model uses only loopback Ollama,
without changing desktop settings or falling back to another provider.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from bughunter.knowledge import KnowledgeService
from bughunter.memory_mappings import MemoryMappings
from bughunter.store import Store

QUOTE = 'Scan the displayed score as i32, change the score in the fixture, then narrow to the new value.'


def run(model=None):
    spec = importlib.util.spec_from_file_location('mapping_native', ROOT / 'src-tauri/resources/mac_memory_mcp.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    value = ctypes.c_int32(127136871)
    address = ctypes.addressof(value)
    reader = native.make_backend()
    reader.open(os.getpid())

    class OwnedWindow:
        """Bounds the test to its own allocation; bytes come from the real OS reader."""
        def regions(self): return [(address, ctypes.sizeof(value), 1)]
        def read(self, start, size):
            if start < address or start + size > address + ctypes.sizeof(value):
                raise AssertionError('Read escaped the owned test value')
            return reader.read(start, size)
        def close(self): reader.close()

    native.S.pid = os.getpid()
    native.S.backend = OwnedWindow()
    with tempfile.TemporaryDirectory(prefix='mapping-native-smoke-') as directory, ExitStack() as patches:
        store = Store(directory)
        knowledge = KnowledgeService(store, start_worker=False)
        try:
            source = knowledge.import_source(filename='fixture-score.md',
                content_base64=base64.b64encode(QUOTE.encode()).decode())
            knowledge.process_source(source['id'])
            settings = {'provider':'ollama', 'model':model or 'fixture', 'local_only':True,
                        'api_base_url':'http://127.0.0.1:11434', 'ollama_context_tokens':4096,
                        'ollama_max_output_tokens':1024, 'ollama_thinking':False, 'ollama_timeout_seconds':240}
            app = SimpleNamespace(store=store, knowledge=knowledge,
                settings=SimpleNamespace(get=lambda: settings),
                research=SimpleNamespace(search=lambda _p: {'id':'fixture','results':[]}))
            service = MemoryMappings(app)
            if not model:
                def propose(_cfg, messages, *_args):
                    ref = json.loads(messages[1]['content'])['references'][0]
                    return json.dumps({'candidates':[{'kind':'value_scan','value_type':'i32',
                        'label':'Fixture score','reference_id':ref['id'],'quote':QUOTE}]})
                patches.enter_context(patch('bughunter.memory_mappings.probe_provider', return_value={'ok':True}))
                patches.enter_context(patch('bughunter.memory_mappings.request_chat', side_effect=propose))
            context = native.mapping_process_context()
            job = service.start('displayed score', context['program'], search_web=False, source_ids=[source['id']])
            deadline = time.monotonic() + 260
            while time.monotonic() < deadline:
                job = service.get(job['id'])
                if job['status'] not in {'researching','suggesting'}: break
                time.sleep(.1)
            assert job['status'] == 'ready', job.get('progress')
            candidate = next(c for c in job['candidates'] if c['recipe']['kind'] == 'value_scan' and c['recipe']['value_type'] == 'i32')
            stages = []
            for score, expected_status in [(127136871, 'matched_once'), (127136859, 'value_verified')]:
                # The fixture changes its own state, equivalent to the operator changing the visible value.
                value.value = score
                check = service.challenge(candidate['id'], context['pid'], context['process_instance_id'], str(score))
                pending = service.pending(check['id'])
                result = json.loads(native.t_verify_mapping({**pending['request'], 'check_id':check['id']}))
                assert result['samples'][0]['address'] == hex(address)
                assert result['samples'][0]['value'] == score
                assert value.value == score
                receipt = {'schema':'bughunter.mapping-receipt/v1','check_id':check['id'],
                           'request':pending['request'],'result':result}
                with Path(pending['receipt_path']).open('x') as stream: json.dump(receipt, stream)
                Path(pending['receipt_path']).chmod(0o600)
                job = service.observe(check['id'])
                child = next(c for c in job['candidates'] if c.get('parent_id') == candidate['id'])
                assert child['status'] == expected_status, child['status']
                stages.append(expected_status)
            restored = MemoryMappings(app, recover=True)
            evidence = json.loads(restored.export(job['id'])['content'])
            assert evidence['verified_count'] == 1
            assert evidence['references'][0]['source_id'] == source['id']
            assert child['quote'] in evidence['references'][0]['text']
            assert restored.observe(check['id'])['verified_count'] == 1
            return {'status':'passed', 'research':'live_local_model' if model else 'fixture_model',
                    'model':model, 'candidate_origin':candidate['origin'], 'warnings':job['warnings'],
                    'native_readback':'passed', 'verification_sequence':stages, 'verified_count':1,
                    'citation_export':'passed', 'restart_and_replay':'passed',
                    'process_path_resolution':'passed', 'external_process_touched':False,
                    'receipt_transport':'test harness; production writer covered by Rust tests',
                    'semantic_or_cross_build_verification':False}
        finally:
            knowledge.shutdown()
            native.S.detach()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='Explicit local Ollama model for a live proposal check')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run(args.model)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
