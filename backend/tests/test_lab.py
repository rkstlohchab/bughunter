import json

import pytest

from bughunter.lab import LabService
from bughunter.policy import PolicyService
from bughunter.store import Store
from bughunter.testing import TestingService
from bughunter.traffic import TrafficService


@pytest.fixture
def lab(tmp_path):
    store = Store(tmp_path); policy = PolicyService(store); traffic = TrafficService(store)
    service = LabService(store,policy,traffic)
    setup = service.setup()
    yield service, setup, policy, TestingService(store,policy)
    service.stop()


@pytest.mark.parametrize("mode,expected", [("vulnerable","confirmed"),("fixed","false_positive"),("display-only","false_positive")])
def test_coupon_exclusion_requires_approval_and_authoritative_evidence(lab, mode, expected):
    service, setup, policy, tests = lab
    traffic = next(t for t in setup["traffic"] if f"/{mode}/" in t["request"]["url"])
    plan = next(t for t in tests.propose(traffic["id"]) if t["title"] == "Check coupon eligibility on excluded items")
    action = tests.queue(plan["id"])
    before = service.server.request_count
    with pytest.raises(ValueError): policy.execute(action["id"])
    assert service.server.request_count == before
    policy.approve(action["id"])
    result = policy.execute(action["id"])
    assert result["status"] == "completed"
    assert len(result["evidence_ids"]) == 1
    assert tests.assess(action["id"])["status"] == expected
    with pytest.raises(ValueError): policy.execute(action["id"])


@pytest.mark.parametrize("body,violation", [
    ({"product_id":"excluded","unit_price":1,"quantity":1},"Client-controlled price accepted"),
    ({"product_id":"regular","quantity":1,"coupon":["ELIGIBLE10","MINIMUM20"]},"Prohibited coupon stacking accepted"),
    ({"product_id":"regular","quantity":1,"coupon":"MINIMUM20"},"Minimum-spend restriction bypassed"),
])
def test_instrumented_pricing_invariants_and_fixed_controls(lab, body, violation):
    service, setup, policy, tests = lab
    for mode in ("vulnerable","fixed"):
        action = policy.queue(setup["project"]["id"], {"method":"POST","url":setup["runtime"]["url"]+f"/{mode}/cart","headers":{"content-type":"application/json"},"body":json.dumps(body)}, "alice")
        policy.approve(action["id"])
        outcome = policy.execute(action["id"])
        data = json.loads(outcome["response"]["body"])
        assert (violation in data["violations"]) is (mode == "vulnerable")


def test_two_account_sessions_remain_separate(lab):
    service, setup, policy, _ = lab
    for account,price in (("alice",10),("bob",30)):
        action = policy.queue(setup["project"]["id"], {"method":"POST","url":setup["runtime"]["url"]+"/vulnerable/cart","headers":{"content-type":"application/json","x-test-account":account},"body":json.dumps({"unit_price":price})}, account)
        policy.approve(action["id"]); policy.execute(action["id"])
    for account,price in (("alice",10),("bob",30)):
        action = policy.queue(setup["project"]["id"], {"method":"GET","url":setup["runtime"]["url"]+"/vulnerable/cart","headers":{"x-test-account":account},"body":""}, account)
        result = policy.execute(action["id"])
        assert json.loads(result["response"]["body"])["accepted_total"] == price


def test_lab_marker_alone_cannot_enable_direct_target_access(lab):
    service, setup, policy, _ = lab
    project = policy.project(setup["project"]["id"])
    project["scope"] = ["http://127.0.0.1:1"]
    policy.save_project(project)
    action = policy.queue(project["id"], {"method":"GET","url":"http://127.0.0.1:1/products","headers":{},"body":""})
    with pytest.raises(ValueError, match="active disposable lab"): policy.execute(action["id"])


def test_quantity_maximum_has_a_vulnerable_oracle_and_fixed_rejection(lab):
    _, setup, policy, _ = lab
    responses = {}
    for mode in ('vulnerable', 'fixed'):
        action = policy.queue(setup['project']['id'], {
            'method':'POST', 'url':setup['runtime']['url']+f'/{mode}/cart',
            'headers':{'content-type':'application/json'},
            'body':json.dumps({'product_id':'regular','quantity':21})}, 'alice')
        policy.approve(action['id'])
        result = policy.execute(action['id'])
        responses[mode] = result['response']
    assert responses['vulnerable']['status'] == 200
    assert 'Invalid quantity accepted' in json.loads(responses['vulnerable']['body'])['violations']
    assert responses['fixed']['status'] == 400
    assert json.loads(responses['fixed']['body'])['error_code'] == 'invalid_quantity'


@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity', '1e400'])
def test_nonfinite_quantity_is_rejected_without_changing_cart(lab, literal):
    service, setup, policy, _ = lab
    for mode in ('vulnerable', 'fixed'):
        baseline = {'account':'alice','accepted_total':100}
        service.server.carts[(mode, 'alice')] = baseline.copy()
        action = policy.queue(setup['project']['id'], {
            'method':'POST', 'url':setup['runtime']['url']+f'/{mode}/cart',
            'headers':{'content-type':'application/json'},
            'body':'{"product_id":"regular","quantity":'+literal+'}'}, 'alice')
        policy.approve(action['id'])
        result = policy.execute(action['id'])
        assert result['status'] == 'completed'
        assert result['response']['status'] == 400
        assert json.loads(result['response']['body'])['error_code'] in {'invalid_quantity','invalid_request'}
        assert service.server.carts[(mode, 'alice')] == baseline
