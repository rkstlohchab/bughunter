"""Source-linked hypotheses and deliberately explicit confirmation criteria."""
from __future__ import annotations

import copy
import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import PolicyService
from .store import Store
from .traffic import TrafficService

CATEGORIES = {
    "business_logic": "Pricing, coupon eligibility, quantities, and workflow invariants",
    "access_control": "Ownership and role comparisons using supplied test accounts",
    "session": "Session binding, expiry, and privilege transitions",
    "input_validation": "Type, boundary, and unexpected-value handling",
    "injection": "Input interpretation and error-based investigation",
    "file_handling": "Upload and download validation",
    "configuration": "Headers and exposed application metadata",
}


def _replace(request: dict, field: dict, value):
    updated = copy.deepcopy(request)
    if field["location"] == "json":
        obj = json.loads(updated["body"])
        parts = re.findall(r"([^\.\[\]]+)|\[(\d+)\]", field["name"])
        cursor = obj
        for name, index in parts[:-1]: cursor = cursor[int(index)] if index else cursor[name]
        name, index = parts[-1]
        cursor[int(index) if index else name] = value
        updated["body"] = json.dumps(obj)
    elif field["location"] == "query":
        u = urlsplit(updated["url"])
        updated["url"] = urlunsplit((u.scheme, u.netloc, u.path, urlencode([(k,str(value) if k == field["name"] else v) for k,v in parse_qsl(u.query, keep_blank_values=True)]), ""))
    elif field["location"] == "form":
        updated["body"] = urlencode([(k,str(value) if k == field["name"] else v) for k,v in parse_qsl(updated["body"], keep_blank_values=True)])
    return updated


class TestingService:
    __test__ = False
    def __init__(self, store: Store, policy: PolicyService, knowledge=None):
        self.store, self.policy, self.knowledge = store, policy, knowledge

    def _citations(self, query: str, project_id: str):
        if not self.knowledge: return []
        try:
            results = self.knowledge.search(query=query, limit=3, project_id=project_id)
            if isinstance(results, dict): results = results.get("results", results.get("items", []))
            return [{k: item.get(k) for k in ("source_id", "title", "locator", "text")} for item in results[:3]]
        except Exception: return []

    def propose(self, traffic_id: str):
        traffic = TrafficService(self.store).get(traffic_id)
        request = traffic["request"]
        project = self.policy.project(traffic["project_id"])
        existing = self.store.list("test", traffic_id=traffic_id)
        if existing: return existing
        suggestions = []
        def add(title, category, field, value, rationale, expected, criteria, prerequisite=None):
            proposed = _replace(request, field, value) if field else copy.deepcopy(request)
            suggestions.append(self.store.put("test", {
                "project_id": traffic["project_id"], "traffic_id": traffic_id, "session_id": traffic.get("session_id", "default"),
                "title": title, "category": category, "field": field["name"] if field else "workflow",
                "location": field["location"] if field else "request", "proposed_change": f"Set {field['name']} to {value!r}" if field else "Compare the baseline with the required account or workflow context",
                "proposed_request": proposed, "rationale": rationale, "prerequisites": prerequisite or [],
                "expected_secure_behavior": expected, "confirmation_criteria": criteria, "status": "proposed",
                "citations": self._citations(f"{category} {title}", traffic["project_id"]), "requires_input": bool(prerequisite),
            }))
        for field in traffic.get("inventory", {}).get("fields", []):
            name, value = field["name"].lower(), field["value"]
            if field["location"] == "header": continue
            if any(word in name for word in ("price", "total", "amount", "cost")) and isinstance(value, (int, float)) and not isinstance(value, bool):
                changed = round(max(0.01, value * 0.5), 2)
                add("Verify server-side price integrity", "business_logic", field, changed,
                    "A client-supplied monetary value may be trusted by a later workflow step.",
                    "The server recalculates the authoritative amount from its catalog and applicable rules.",
                    "An authoritative cart/order record accepts the modified amount despite a different valid server-calculated total; a display change alone does not qualify.")
            if any(word in name for word in ("coupon", "promo", "discount_code")):
                coupon = "ELIGIBLE10" if project.get("lab_project") else "SUPPLIED_TEST_COUPON"
                add("Check coupon eligibility on excluded items", "business_logic", field, coupon,
                    "Coupon validation may check only the code and omit product/category eligibility.",
                    "Excluded products retain their full price while eligible products receive the documented discount.",
                    "Compare eligible and excluded control carts and verify the excluded item receives a server-accepted discount.",
                    [] if project.get("lab_project") else ["Supply a valid test coupon and a known excluded item; edit the proposed request before queuing."])
                add("Check coupon stacking and reuse", "business_logic", field, ["ELIGIBLE10", "MINIMUM20"] if project.get("lab_project") else "SUPPLIED_TEST_COUPON",
                    "Separate coupon application steps may skip exclusivity and use-count rules.",
                    "The server enforces the program's stacking and reuse restrictions at checkout.",
                    "An authoritative order shows prohibited combined discounts or repeat use, with single-coupon controls.",
                    [] if project.get("lab_project") else ["Provide permitted stacking/reuse rules and valid test coupons."])
            if any(word in name for word in ("quantity", "qty")) and isinstance(value, (int, float)) and not isinstance(value, bool):
                add("Check quantity boundary validation", "input_validation", field, -1,
                    "Quantity boundaries can affect inventory and amount calculations.",
                    "Invalid quantities are rejected and server totals and inventory remain consistent.",
                    "A server-side record accepts an invalid quantity with a demonstrable incorrect amount or inventory effect.")
                add("Recheck minimum spend after cart changes", "business_logic", field, 1,
                    "A discount may remain after the cart no longer satisfies its minimum-spend condition.",
                    "The server recomputes eligibility after every relevant cart change and at checkout.",
                    "A below-threshold cart retains a server-accepted restricted discount; compare with a valid-threshold control.",
                    ["Start with an eligible cart and supply the documented minimum-spend coupon."])
            if re.search(r"(?:^|[._])(user|account|cart|order|owner|customer)_?id$", name):
                add("Compare resource ownership across test accounts", "access_control", field, "SECOND_TEST_ACCOUNT_RESOURCE_ID",
                    "The server must bind resource identifiers to the authenticated account.",
                    "The second account cannot read or modify the first account's private resource.",
                    "Two supplied account sessions demonstrate an unauthorized resource disclosure or accepted modification.",
                    ["Supply two authorized test accounts and a resource owned by the other account."])
            if isinstance(value, str) and value and name not in {"query", "password", "token", "csrf"} and not any(w in name for w in ("coupon", "promo")):
                add("Compare input interpretation and validation", "injection", field, value + "'",
                    "A small syntax variation can reveal an input-processing boundary requiring further investigation.",
                    "The input remains data and errors do not disclose sensitive implementation information.",
                    "Reproduce a security impact with appropriate controls; an error or status difference is only a lead.")
        headers = {k.lower():v for k,v in request.get("headers", {}).items()}
        if "cookie" in headers or "authorization" in headers:
            add("Verify session and account binding", "session", None, None,
                "Session credentials should retain the expected identity and privilege across the workflow.",
                "Expired or mismatched credentials cannot access protected resources.",
                "Controlled account/session comparisons prove an access violation; differing responses alone are insufficient.",
                ["Supply a second authorized session or an expired-session control."])
        if "multipart" in str(headers.get("content-type", "")) or any(w in request["url"].lower() for w in ("upload", "download", "file")):
            add("Review file handling boundaries", "file_handling", None, None,
                "File operations require content, path, ownership, and serving-policy validation.",
                "Only permitted content and owned resources are accepted and served safely.",
                "A permitted benign test file demonstrates a reproducible policy violation.",
                ["Provide a harmless test file and the application's accepted-file policy."])
        add("Review response security configuration", "configuration", None, None,
            "Response headers and metadata can reveal configuration weaknesses worth evaluating in application context.",
            "Security-relevant headers and sensitive metadata match the application's requirements.",
            "Demonstrate application-specific impact; a missing header alone is not a confirmed vulnerability.")
        return suggestions

    def queue(self, id: str, request: dict | None = None):
        test = self.store.get("test", id)
        if not test: raise ValueError("Test not found")
        if test.get("requires_input") and request is None:
            raise ValueError("Complete the listed prerequisites and supply an edited request before queuing")
        chosen = request or test["proposed_request"]
        if any(marker in json.dumps(chosen) for marker in ("SUPPLIED_TEST_COUPON", "SECOND_TEST_ACCOUNT_RESOURCE_ID")):
            raise ValueError("Replace test placeholders with authorized test values")
        action = self.policy.queue(test["project_id"], chosen, test.get("session_id", "default"), test_id=id)
        self.store.put("test", {**test, "status": "queued", "action_id": action["id"]})
        return action

    def coverage(self, project_id: str):
        tests = self.store.list("test", project_id=project_id)
        actions = {a.get("test_id"):a for a in self.store.list("action",project_id=project_id) if a.get("test_id")}
        coverage = []
        for key, description in CATEGORIES.items():
            plans = [t for t in tests if t["category"] == key]
            executed = sum(actions.get(t["id"],{}).get("status") == "completed" for t in plans)
            blocked = sum(actions.get(t["id"],{}).get("status") in {"denied","cancelled","failed","unknown"} or (t.get("requires_input",False) and not t.get("action_id")) for t in plans)
            coverage.append({"category":key,"description":description,"proposed":len(plans),"executed":executed,"blocked":blocked,
                "untested":len(plans)-executed,"status":"tested" if executed else "blocked" if blocked else "untested"})
        coverage.append({"category":"unsupported","description":"WebSockets, raw protocols, mobile/network attacks, arbitrary scripts and templates","proposed":0,"executed":0,"blocked":0,"untested":0,"status":"unsupported"})
        return coverage

    def assess(self, action_id: str):
        action = self.store.get("action", action_id)
        if not action or action["status"] != "completed": raise ValueError("Complete the action before assessing evidence")
        project = self.policy.project(action["project_id"])
        response = action.get("response") or {}
        if not isinstance(response, dict): response = {}
        try: outcome = json.loads(response.get("body", ""))
        except (ValueError, TypeError): outcome = {}
        if not isinstance(outcome, dict): outcome = {}
        # A lab marker establishes neither a complete oracle result nor a negative
        # finding. Rejections, truncated responses and absent checks remain unknown.
        violations = outcome.get("violations")
        totals = [outcome.get(key) for key in ("accepted_total", "canonical_total")]
        valid_oracle = (isinstance(violations, list)
            and all(isinstance(v, str) and v.strip() for v in violations)
            and all(type(v) is int or (type(v) is float and math.isfinite(v)) for v in totals)
            and (bool(violations) or totals[0] == totals[1]))
        status_code = response.get("status")
        receipts = [self.store.get("traffic", eid) for eid in action.get("evidence_ids", [])]
        linked_receipt = any(r and r.get("project_id") == project["id"]
            and r.get("action_id") == action_id and r.get("trusted_observation") is True
            and r.get('origin') == 'gateway'
            and isinstance(action.get('request'), dict)
            and r.get('request') == action['request']
            and r.get('session_id') == action.get('session_id')
            and r.get("response") == response for r in receipts)
        verified_lab = (project.get("lab_project") and action.get("lab_verified")
            and outcome.get("lab_evidence") is True and valid_oracle and linked_receipt
            and type(status_code) is int and 200 <= status_code < 300
            and not response.get("truncated") and 'error' not in outcome)
        violations = outcome.get("violations", []) if verified_lab else []
        status = "confirmed" if violations else "false_positive" if verified_lab else "inconclusive"
        plan = self.store.get("test",action.get("test_id","")) or {}
        finding = self.store.put("finding", {"project_id": action["project_id"], "title": " / ".join(violations) if violations else "Request outcome requires investigation",
            "status": status, "summary": outcome.get("explanation", "The response is an observation; attach controls and authoritative evidence before confirming impact."),
            "impact": "The controlled shop accepted an incorrect pricing or eligibility outcome." if violations else "No security impact established.",
            "steps": ["Capture the baseline using the designated test account.", "Review and send the proposed request.", "Inspect authoritative response and compare the fixed control."],
            "evidence_ids": action.get("evidence_ids", []), "action_id": action_id, "confirmation_method": "instrumented_lab" if verified_lab else "manual_review",
            "citations":plan.get("citations",[]),
            "confirmation": "The instrumented local shop reported server-accepted violations: " + "; ".join(violations) if violations else "No unauthorized server-accepted outcome established.",
            "controls": "The local shop oracle compares persisted server calculations with its product eligibility and pricing rules. Display-only changes are excluded." if verified_lab else "Record a baseline or account control during manual review."})
        if action.get("test_id"):
            test = self.store.get("test", action["test_id"])
            if test: self.store.put("test", {**test, "status": "tested", "finding_id": finding["id"]})
        return finding

    def dispatch(self, method: str, params: dict):
        if method == "tests.list": return self.store.list("test", **params)
        if method == "tests.propose": return self.propose(**params)
        if method == "tests.queue": return self.queue(**params)
        if method == "tests.coverage": return self.coverage(**params)
        if method == "tests.assess": return self.assess(**params)
        raise ValueError("Unknown testing operation")
