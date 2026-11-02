"""P7 smoke-test safety, truthful status, persistence, and cancellation tests."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bughunter.store import Store
from bughunter.readiness_p7 import ReleaseReadinessP7, SUITES, _redact


class FakeSettings:
    def get(self):
        return {"provider": "ollama", "model": "qwen3:8b", "api_base_url": "http://localhost:11434"}


def ready(tmp_path):
    app = SimpleNamespace(store=Store(tmp_path / 'state'), settings=FakeSettings())
    return ReleaseReadinessP7(app)


def until(service, run_id, max_wait=15):
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        rows = service.list_runs()
        found = next((r for r in rows if r['id']==run_id), None)
        if found and found['status'] not in {'queued', 'running'}:
            return found
        time.sleep(.04)
    pytest.fail('verification did not finish within deadline')


def test_preflight_never_claims_mac_verified(tmp_path):
    service = ready(tmp_path)
    row = service.preflight()
    assert row['phase'] == 'P7'
    assert row['interpretation'].startswith('Installed ≠ connected')
    assert {x['id'] for x in row['checks']} >= {'mac', 'source', 'model', 'burp', 'tor', 'course', 'memory'}
    assert next(x for x in row['checks'] if x['id']=='burp')['state']=='not_verified'
    assert all('api_base_url' not in json.dumps(x) for x in row['checks'])
    assert len(row['suites']) == 5


def test_approval_scope_and_unknown_method(tmp_path):
    service = ready(tmp_path)
    with pytest.raises(ValueError, match='Confirm'):
        service.dispatch('readiness.p7.start', {'suite':'memory_lab'})
    with pytest.raises(ValueError, match='Unknown verification'):
        service.dispatch('readiness.p7.start', {'suite':'custom_command', 'approved':True})
    with pytest.raises(ValueError, match='Unknown P7'):
        service.dispatch('readiness.p7.unknown', {})
    assert not service.list_runs()


def test_persisted_pass_fail_and_export(tmp_path, monkeypatch):
    service = ready(tmp_path)
    # Exact suite command substitutions in the test only; production list is fixed.
    monkeypatch.setitem(SUITES, 'fast_pass', ('Quick fixture pass', ['python', '-c', 'print("PASS")'], 10))
    monkeypatch.setitem(SUITES, 'fast_fail', ('Quick fixture failure', ['python', '-c', 'raise SystemExit(4)'], 10))
    row = service.start('fast_pass', True)
    passed = until(service, row['id'])
    assert passed['status'] == 'passed' and passed['exit_code'] == 0
    assert 'PASS' in passed['output']
    row = service.start('fast_fail', True)
    failed = until(service, row['id'])
    assert failed['status'] == 'failed' and failed['exit_code'] == 4
    path = service.export()['path']
    exported = json.loads(Path(path).read_text())
    assert exported['live_mac_verified'] is False
    assert {r['status'] for r in exported['runs']} >= {'passed','failed'}
    assert service.store.get('p7_run', row['id'])['status']=='failed'


def test_concurrent_jobs_stopped_and_recovered(tmp_path, monkeypatch):
    service = ready(tmp_path)
    monkeypatch.setitem(SUITES, 'sleep', ('Delay fixture', ['python', '-c', 'import time;time.sleep(10)'], 15))
    run = service.start('sleep', True)
    with pytest.raises(ValueError, match='already running'):
        service.start('sleep', True)
    assert service.stop(run['id'], approved=True)['stopping']
    assert until(service,run['id'])['status']=='cancelled'
    interrupted = service.store.put('p7_run', {'suite':'memory_lab','status':'running','output':''})
    resumed = ReleaseReadinessP7(SimpleNamespace(store=service.store, settings=FakeSettings()))
    assert resumed.store.get('p7_run',interrupted['id'])['status']=='interrupted'


def test_redaction():
    assert 'abcd' not in _redact('token=abcd')
    assert 'abc123' not in _redact('Authorization: bearer abc123')
    assert 'OK' in _redact('OK')


def test_explicit_probes_never_auto_contact_external_services(tmp_path):
    service=ready(tmp_path)
    with pytest.raises(ValueError, match='Confirm'):
        service.dispatch('readiness.p7.probe', {'target':'tor'})
    with pytest.raises(ValueError, match='Unknown integration'):
        service.dispatch('readiness.p7.probe', {'target':'arbitrary_web', 'approved':True})
    service.app.tor = SimpleNamespace(status=lambda: {'connected':True, 'socks_port':9050})
    assert service.dispatch('readiness.p7.probe',{'target':'tor', 'approved':True})['ok']
    service.app.burp_history = SimpleNamespace(dispatch=lambda method,p: {'connected':False})
    assert not service.dispatch('readiness.p7.probe',{'target':'burp', 'approved':True})['ok']
    assert [x['target'] for x in service.probes()][:2] == ['burp','tor']
    assert len(service.export()['report']['probes']) == 2


def test_agent_can_inspect_but_cannot_run_smoke_tests(tmp_path):
    from bughunter.model_tools import ModelTools, TOOL_SPECS
    service=ready(tmp_path)
    app=service.app
    app.readiness_p7=service
    run=app.store.put('run',{'status':'running','project_id':None})
    model=ModelTools(app,run['id'],study=True)
    assert 'get_runtime_readiness' in {x['name'] for x in TOOL_SPECS}
    report=model.call('get_runtime_readiness',{})
    assert report['phase']=='P7'
    assert 'detail' not in report['checks'][0]
    assert 'api_base_url' not in json.dumps(report)
    with pytest.raises(ValueError,match='not available'):
        model.call('readiness.p7.start',{'suite':'memory_lab','approved':True})
