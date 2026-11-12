import json

import pytest

from bughunter.policy import PolicyService
from bughunter.store import Store
from bughunter.testing import TestingService
from bughunter.traffic import TrafficService


def test_source_linked_suggestions_deduplicate_and_require_placeholders(tmp_path):
    store=Store(tmp_path); policy=PolicyService(store); traffic=TrafficService(store)
    project=policy.save_project({"name":"Example", "scope":["https://example.com"], "authorization":"Authorized"})
    item=traffic.record(project["id"], {"method":"POST","url":"https://example.com/cart","headers":{"content-type":"application/json"},"body":json.dumps({"price":20,"coupon":"","quantity":1,"cart_id":"alice"})})
    class Knowledge:
        def search(self,query,limit,project_id): return [{"source_id":"lesson", "title":"Coupon rules","locator":"page 4","text":"Validate item eligibility at checkout."}]
    tests=TestingService(store,policy,Knowledge())
    proposals=tests.propose(item["id"])
    assert all(p["citations"][0]["locator"] == "page 4" for p in proposals)
    assert tests.propose(item["id"]) == proposals[::-1] or len(tests.propose(item["id"])) == len(proposals)
    coupon=next(p for p in proposals if p["title"] == "Check coupon eligibility on excluded items")
    with pytest.raises(ValueError, match="prerequisites"): tests.queue(coupon["id"])
    price=next(p for p in proposals if p["title"] == "Verify server-side price integrity")
    action=tests.queue(price["id"])
    assert json.loads(action["request"]["body"])["price"] == 10
    assert action["status"] == "pending_approval"
    assert {c["category"] for c in tests.coverage(project["id"])} >= {"session","injection","business_logic"}


def test_arbitrary_target_response_cannot_self_confirm(tmp_path):
    store=Store(tmp_path); policy=PolicyService(store)
    project=policy.save_project({"name":"Example", "scope":["https://example.com"], "authorization":"Authorized"})
    action=store.put("action", {"project_id":project["id"],"status":"completed","evidence_ids":[],"response":{"body":json.dumps({"lab_evidence":True,"violations":["fake"]})}})
    finding=TestingService(store,policy).assess(action["id"])
    assert finding["status"] == "inconclusive"


def recorded_lab_result(tmp_path, body, *, status=200, truncated=False, receipt=True):
    store = Store(tmp_path)
    policy = PolicyService(store)
    project = policy.save_project({"name":"Fixture", "scope":["https://example.test"],
                                   "authorization":"Fixture", "lab_project":True})
    response = {"status":status, "body":json.dumps(body), "truncated":truncated}
    request = {"method":"POST", "url":"https://example.test/fixed/cart", "headers":{}, "body":"{}"}
    action = store.put("action", {"project_id":project["id"], "status":"completed",
                                  "lab_verified":True, "request":request, "session_id":"alice",
                                  "response":response, "evidence_ids":[]})
    if receipt:
        evidence = TrafficService(store).record(project["id"],
            request, response, session_id='alice', origin='gateway', action_id=action["id"], trusted_observation=True)
        action = store.put("action", {**action, "evidence_ids":[evidence["id"]]})
    return store, TestingService(store, policy), action


@pytest.mark.parametrize("body", [
    [], None, "reflected input", {"lab_evidence":True},
    {"lab_evidence":True, "violations":None},
    {"lab_evidence":True, "violations":"Client-controlled price accepted"},
    {"lab_evidence":True, "violations":[42]},
    {"lab_evidence":True, "violations":[""]},
    {"lab_evidence":True, "violations":[]},
    {"lab_evidence":True, "violations":[], "accepted_total":9, "canonical_total":10},
    {"lab_evidence":True, "violations":[], "accepted_total":float('nan'), "canonical_total":10},
    {"lab_evidence":True, "violations":[], "accepted_total":float('inf'), "canonical_total":10},
    {"lab_evidence":True, "violations":[], "accepted_total":True, "canonical_total":1},
])
def test_incomplete_lab_oracle_does_not_claim_negative_or_confirmed_result(tmp_path, body):
    _, service, action = recorded_lab_result(tmp_path, body)
    result = service.assess(action['id'])
    assert result['status'] == 'inconclusive'
    assert result['confirmation_method'] == 'manual_review'


@pytest.mark.parametrize("status,truncated,receipt,error", [
    (400,False,True,None), (500,False,True,None), (200,True,True,None),
    (200,False,False,None), (200,False,True,'Invalid request'),
])
def test_lab_result_requires_successful_complete_recorded_execution(tmp_path, status, truncated, receipt, error):
    body = {"lab_evidence":True, "violations":[], "accepted_total":10, "canonical_total":10}
    if error: body['error'] = error
    _, service, action = recorded_lab_result(tmp_path, body, status=status, truncated=truncated, receipt=receipt)
    assert service.assess(action['id'])['status'] == 'inconclusive'


@pytest.mark.parametrize("change", ['foreign_project', 'different_action', 'untrusted', 'different_response',
                                    'different_request', 'different_session', 'imported'])
def test_lab_result_rejects_unrelated_or_changed_receipt(tmp_path, change):
    body = {"lab_evidence":True, "violations":[], "accepted_total":10, "canonical_total":10}
    store, service, action = recorded_lab_result(tmp_path, body)
    receipt = store.get('traffic', action['evidence_ids'][0])
    changes = {'foreign_project':{'project_id':'another-project'},
               'different_action':{'action_id':'another-action'},
               'untrusted':{'trusted_observation':False},
               'different_request':{'request':{'method':'GET','url':'https://example.test/other'}},
               'different_session':{'session_id':'bob'},
               'imported':{'origin':'imported'},
               'different_response':{'response':{'status':200,'body':'{}'}}}
    store.put('traffic', {**receipt, **changes[change]})
    assert service.assess(action['id'])['status'] == 'inconclusive'


@pytest.mark.parametrize("violations,accepted,status", [
    ([],10,'false_positive'), (['Client-controlled price accepted'],1,'confirmed'),
])
def test_complete_lab_cart_evidence_retains_oracle_classification(tmp_path, violations, accepted, status):
    _, service, action = recorded_lab_result(tmp_path,
        {"lab_evidence":True, "violations":violations, "accepted_total":accepted, "canonical_total":10})
    assert service.assess(action['id'])['status'] == status


@pytest.mark.parametrize("body", ["SQL syntax error near input", '<script>testMarker()</script>'])
def test_external_error_or_reflection_is_not_execution_evidence(tmp_path, body):
    store = Store(tmp_path)
    policy = PolicyService(store)
    project = policy.save_project({"name":"External", "scope":["https://example.test"], "authorization":"Fixture"})
    action = store.put('action', {'project_id':project['id'], 'status':'completed',
                                 'response':{'status':200, 'body':body}, 'evidence_ids':[]})
    assert TestingService(store, policy).assess(action['id'])['status'] == 'inconclusive'


def test_proposed_test_citations_cannot_read_another_projects_library(tmp_path):
    from bughunter.knowledge import KnowledgeService
    store = Store(tmp_path)
    policy = PolicyService(store)
    knowledge = KnowledgeService(store)
    project = policy.save_project({"name":"Own", "scope":["https://example.test"], "authorization":"Fixture"})
    sources = [store.put('source', {'title':'Pricing coupon rules', 'project_id':scope})
               for scope in (None, project['id'], 'foreign-project')]
    for source in sources:
        knowledge._index(source, [{'text':'Verify server-side price integrity and coupon eligibility business logic.', 'locator':'page 1'}])
    traffic = TrafficService(store).record(project['id'],
        {'method':'POST','url':'https://example.test/cart','headers':{},'body':'{"price":10}'})
    proposals = TestingService(store,policy,knowledge).propose(traffic['id'])
    citations = {c['source_id'] for test in proposals for c in test['citations']}
    assert sources[2]['id'] not in citations
    assert {sources[0]['id'],sources[1]['id']} <= citations
