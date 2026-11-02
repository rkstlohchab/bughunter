"""Bounded real-HTTP regression tests; never connect to a public host."""
import time
from types import SimpleNamespace

import pytest
from bughunter.commerce_audit import CommerceAudit, CASES
from bughunter.lab import LabService
from bughunter.policy import PolicyService
from bughunter.reports import ReportService
from bughunter.store import Store
from bughunter.traffic import TrafficService


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path)
    policy = PolicyService(store)
    traffic = TrafficService(store)
    lab = LabService(store, policy, traffic)
    fake = SimpleNamespace(store=store, policy=policy, traffic=traffic, lab=lab,
                           reports=ReportService(store), tasks=SimpleNamespace(describe=lambda row: row))
    audit = CommerceAudit(fake)
    yield audit, fake
    lab.stop()


def await_done(store, id, timeout=12):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        row = store.get('work_task', id)
        if row['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
            return row
        time.sleep(.08)
    pytest.fail('Cart regression worker did not finish in time')


def test_cart_audit_requires_operator_consent(fixture):
    runner, app = fixture
    with pytest.raises(ValueError, match='explicit authorization'):
        runner.start()
    assert not app.store.list('work_task')
    assert app.lab.server is None


def test_cart_audit_uses_real_loopback_actions_and_fixed_controls(fixture):
    runner, app = fixture
    started = runner.start(approved=True)
    done = await_done(app.store, started['id'])
    assert done['status'] == 'completed', done.get('error')
    result = done['result']
    assert len(result['cases']) == 2*len(CASES)
    assert result['observed_failures'] == len(CASES)
    assert result['passed_controls'] == len(CASES)
    assert len(result['findings']) == len(CASES)
    assert app.lab.server.request_count >= 2*len(CASES)
    actions = app.store.list('action', project_id=started['project_id'])
    assert len(actions) == 2*len(CASES)
    assert all(action['status'] == 'completed' for action in actions)
    assert all(action.get('evidence_ids') for action in actions)
    assert {e['variant'] for e in result['cases']} == {'vulnerable', 'fixed'}
    assert all(e['passed'] for e in result['cases'])
    assert all(app.store.get('finding', fid)['status'] == 'observed' for fid in result['findings'])
    assert all(action['request']['url'].startswith('http://127.0.0.1:') for action in actions)


def test_reject_active_emergency_stop(fixture):
    runner, app = fixture
    app.store.set_setting('emergency_stop', True)
    with pytest.raises(ValueError, match='Emergency stop'):
        runner.start(approved=True)
    assert not app.store.list('action')


def test_cart_audit_cannot_be_redirected_to_arbitrary_website(fixture):
    runner, app = fixture
    with pytest.raises(TypeError):
        runner.start(approved=True, target_url='https://external-shop.example/checkout')
    assert not app.store.list('action')
