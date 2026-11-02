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
        def search(self,query,limit): return [{"source_id":"lesson", "title":"Coupon rules","locator":"page 4","text":"Validate item eligibility at checkout."}]
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
