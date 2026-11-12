"""Bounded real-HTTP regression tests; never connect to a public host."""
import copy
import json
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
    food_actions = [a for a in actions if a['request']['url'].endswith('/food-cart')]
    assert len(food_actions) == 14
    for action in food_actions:
        data = json.loads(action['response']['body'])
        if action['response']['status'] == 200:
            assert data['quote_only'] and not data['order_created'] and not data['payment_initiated']
            assert type(data['accepted_total_minor']) is int


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


@pytest.mark.parametrize('fault', [
    'http_500', 'error_json', 'malformed_json', 'array_json', 'missing_contract',
    'wrong_total', 'wrong_account', 'wrong_action', 'wrong_project',
    'wrong_receipt_response', 'untrusted_receipt', 'truncated',
])
def test_broken_fixed_response_or_receipt_never_passes(fixture, monkeypatch, fault):
    runner, app = fixture
    monkeypatch.setattr('bughunter.commerce_audit.CASES', (CASES[0],))
    execute = app.policy.execute

    def damaged_receipt(action_id):
        result = execute(action_id)
        if '/fixed/' not in result['request']['url']:
            return result
        result = copy.deepcopy(result)
        body = json.loads(result['response']['body'])
        if fault == 'http_500':
            result['response']['status'] = 500
        elif fault == 'truncated':
            result['response']['truncated'] = True
        elif fault == 'error_json':
            body['error'] = 'Internal calculation failed'
        elif fault == 'malformed_json':
            result['response']['body'] = '{'
        elif fault == 'array_json':
            body = []
        elif fault == 'missing_contract':
            body.pop('lab_evidence')
        elif fault == 'wrong_total':
            body['accepted_total'] = 1
        elif fault == 'wrong_account':
            body['account'] = 'bob'
        if fault != 'malformed_json':
            result['response']['body'] = json.dumps(body)
        receipt = app.store.get('traffic', result['evidence_ids'][0])
        receipt['response'] = copy.deepcopy(result['response'])
        if fault == 'wrong_action': receipt['action_id'] = 'another_action'
        elif fault == 'wrong_project': receipt['project_id'] = 'another_project'
        elif fault == 'wrong_receipt_response': receipt['response']['body'] = '{}'
        elif fault == 'untrusted_receipt': receipt['trusted_observation'] = False
        app.store.put('traffic', receipt)
        app.store.put('action', result)
        return result

    monkeypatch.setattr(app.policy, 'execute', damaged_receipt)
    task = runner.start(approved=True)
    done = await_done(app.store, task['id'])
    assert done['status'] == 'failed'
    assert done['result']['passed_controls'] == 0


def test_unrelated_400_error_does_not_count_as_quantity_rejection(fixture, monkeypatch):
    runner, app = fixture
    monkeypatch.setattr('bughunter.commerce_audit.CASES', (CASES[-1],))
    execute = app.policy.execute

    def unrelated_error(action_id):
        result = execute(action_id)
        if '/fixed/' in result['request']['url']:
            body = json.loads(result['response']['body'])
            body.update(error='Temporary dependency failure', error_code='unavailable')
            result['response']['body'] = json.dumps(body)
            receipt = app.store.get('traffic', result['evidence_ids'][0])
            receipt['response'] = result['response']
            app.store.put('traffic', receipt)
            app.store.put('action', result)
        return result

    monkeypatch.setattr(app.policy, 'execute', unrelated_error)
    task = runner.start(approved=True)
    done = await_done(app.store, task['id'])
    assert done['status'] == 'failed'
    assert done['result']['passed_controls'] == 0
