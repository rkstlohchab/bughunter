"""Milestone 3: the procedural-memory promotion loop (learn -> apply -> learn).

When an autonomous AGENT run completes successfully, RunManager._maybe_promote_skill
auto-drafts ONE reusable-method CANDIDATE from what the run actually did (a plan with a
completed subtask, plus evidence SkillService.propose accepts). It is strictly additive:
a single store write of a 'candidate' reviewed_skill linked to real evidence. It adds no
execution power and is NEVER auto-verified, so SkillService.match / relevant_methods will
not recall it until a human reviews it. It never fabricates, and a failure inside it can
never change the run's outcome.

These drive the real RunManager (_maybe_promote_skill directly, and _work end-to-end with
only the Ollama HTTP transport stubbed, as in test_orchestrator_plan.py). No billing,
gateway, or approval path is involved: planning + update_plan are run-local, read/write
only (AGENTS.md invariant 1 is untouched).
"""
import re
import threading

import pytest

from bughunter.app import Application
from bughunter.providers import DEFAULT_SETTINGS
import bughunter.providers as providers


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    application.store.put("project", {"id": "p", "name": "Owned", "scope": ["https://e.test"], "authorization": "ok"})
    yield application
    application.shutdown()


def _agent_run(app, **overrides):
    return app.store.put("run", {"kind": "agent", "autonomous": True, "project_id": "p", "status": "completed",
        "provider": "ollama", "model": "m", "prompt": "Investigate the coupon endpoint for IDOR",
        "summary": "Probed the endpoint and drafted a lead.", "source_ids": [], "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}, **overrides})


def _plan(app, run, statuses):
    """statuses: list of (goal, status); returns the stored plan entity."""
    subtasks = [{"id": f"s{i}", "goal": goal, "status": status} for i, (goal, status) in enumerate(statuses, 1)]
    return app.store.put("plan", {"run_id": run["id"], "objective": run.get("prompt", ""), "subtasks": subtasks})


def _finding(app, **overrides):
    return app.store.put("finding", {"project_id": "p", "title": "Coupon IDOR lead", "status": "hypothesis",
        "summary": "s", "evidence_ids": [], **overrides})


# ---- a grounded completion drafts exactly one candidate -----------------------

def test_completed_agent_run_promotes_one_candidate_from_finding(app):
    app.store.put("source", {"id": "src-unused", "project_id": "p", "title": "Course"})
    run = _agent_run(app, source_ids=["src-unused"])
    _plan(app, run, [("Map the coupon API surface", "done"), ("Attempt a write", "blocked")])
    finding = _finding(app)

    app.runs._maybe_promote_skill(run)

    cards = app.store.list("reviewed_skill")
    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "candidate"                      # never auto-verified
    assert "autodrafted" in card["tags"]
    assert card["project_id"] == "p"
    assert card["evidence_id"] == finding["id"]               # a finding is preferred over a source
    assert len(card["title"].strip()) >= 4
    assert "coupon endpoint" in card["title"].lower()         # title derived from the objective
    assert "Map the coupon API surface" in card["method"]     # the DONE subtask is synthesized in
    assert "Attempt a write" not in card["method"]            # a non-done subtask is not claimed as done
    assert "Probed the endpoint" in card["method"]            # the run's own recorded summary, not invented


def test_promotes_candidate_with_source_fallback_when_no_finding(app):
    app.store.put("source", {"id": "src1", "project_id": "p", "title": "Coupon course"})
    run = _agent_run(app, source_ids=["src1"])
    _plan(app, run, [("Recon the API", "done")])

    app.runs._maybe_promote_skill(run)

    cards = app.store.list("reviewed_skill")
    assert len(cards) == 1
    assert cards[0]["status"] == "candidate" and cards[0]["evidence_id"] == "src1"


def test_prefers_a_finding_produced_by_this_run(app):
    run = _agent_run(app)
    _plan(app, run, [("Recon", "done")])
    this_run_finding = _finding(app, title="This run's finding", run_id=run["id"])   # created first (older)
    _finding(app, title="Unrelated newer finding")                                   # created later (most recent)

    app.runs._maybe_promote_skill(run)

    cards = app.store.list("reviewed_skill")
    assert len(cards) == 1
    assert cards[0]["evidence_id"] == this_run_finding["id"]  # this run's own finding wins over a newer one


# ---- a candidate is NOT recalled until a human reviews it ---------------------

def test_candidate_is_not_recalled_until_reviewed(app):
    run = _agent_run(app)
    _plan(app, run, [("Map the coupon API surface", "done")])
    _finding(app)
    app.runs._maybe_promote_skill(run)
    card = app.store.list("reviewed_skill")[0]
    assert card["status"] == "candidate"

    # match() returns verified-only, and relevant_methods (which primes autonomous runs)
    # injects nothing for a mere candidate.
    assert app.skills.match("coupon endpoint idor", project_id="p") == []
    assert providers.relevant_methods(app, "coupon endpoint idor", "p") == ""

    # After a human reviews and verifies it, it becomes eligible for recall.
    app.skills.review(id=card["id"], verified=True, note="Reproduced in the disposable lab shop.")
    matched = app.skills.match("coupon endpoint idor", project_id="p")
    assert any(row["id"] == card["id"] for row in matched)
    assert "learned earlier" in providers.relevant_methods(app, "coupon endpoint idor", "p")


# ---- no grounding -> no fabrication ------------------------------------------

def test_no_candidate_without_a_plan(app):
    run = _agent_run(app)
    _finding(app)
    app.runs._maybe_promote_skill(run)
    assert app.store.list("reviewed_skill") == []


def test_no_candidate_without_a_done_subtask(app):
    run = _agent_run(app)
    _plan(app, run, [("Recon", "blocked"), ("Probe", "active"), ("Summarize", "pending")])
    _finding(app)
    app.runs._maybe_promote_skill(run)
    assert app.store.list("reviewed_skill") == []


def test_no_candidate_without_acceptable_evidence(app):
    run = _agent_run(app, source_ids=["does-not-exist"])  # no finding, referenced source absent
    _plan(app, run, [("Recon", "done")])
    app.runs._maybe_promote_skill(run)
    assert app.store.list("reviewed_skill") == []


# ---- scope: only autonomous agent runs ---------------------------------------

def test_study_and_investigation_runs_do_not_promote(app):
    study = app.store.put("run", {"kind": "study", "project_id": "p", "source_id": "src", "status": "completed",
        "provider": "ollama", "model": "m", "prompt": "Study", "summary": "done",
        "settings_snapshot": {**DEFAULT_SETTINGS}})
    investigation = app.store.put("run", {"project_id": "p", "status": "completed", "provider": "ollama",
        "model": "m", "prompt": "Investigate", "summary": "done", "settings_snapshot": {**DEFAULT_SETTINGS}})
    for run in (study, investigation):
        _plan(app, run, [("Recon", "done")])
        _finding(app)
        app.runs._maybe_promote_skill(run)
    assert app.store.list("reviewed_skill") == []


def test_agent_run_without_project_does_not_promote(app):
    run = _agent_run(app, project_id=None)
    _plan(app, run, [("Recon", "done")])
    app.runs._maybe_promote_skill(run)
    assert app.store.list("reviewed_skill") == []


# ---- idempotent: at most one candidate per run -------------------------------

def test_promotion_is_idempotent_for_the_same_run(app):
    run = _agent_run(app)
    _plan(app, run, [("Recon the API", "done")])
    _finding(app)
    app.runs._maybe_promote_skill(run)
    app.runs._maybe_promote_skill(run)
    assert len(app.store.list("reviewed_skill")) == 1


# ---- the hook fires on a real _work completion -------------------------------

class _Resp:
    def __init__(self, payload): self._p, self.status_code = payload, 200
    def raise_for_status(self): pass
    def json(self): return self._p


class _PromoClient:
    """httpx.Client stand-in for the Ollama path: the planner returns one subtask, the
    agent's first turn marks it done via update_plan (reading its generated id out of the
    plan injection), and the run then finishes - enough to drive a real _work completion."""
    def __init__(self, planner_reply='["Recon the coupon API"]'):
        self.planner_reply, self.marked = planner_reply, False

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def post(self, url, json=None, headers=None):
        body = json or {}
        if url.endswith("/api/show"):
            return _Resp({"capabilities": ["tools"]})
        content = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
        if "tools" not in body and "Decompose the objective" in content:
            return _Resp({"message": {"role": "assistant", "content": self.planner_reply}, "usage": {}})
        if not self.marked:
            ids = re.findall(r"\[(sub_[0-9a-f]+)\]", content)
            if ids:
                self.marked = True
                message = {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "update_plan", "arguments": {"subtask_id": ids[0], "status": "done"}}}]}
                return _Resp({"message": message, "usage": {}})
        return _Resp({"message": {"role": "assistant", "content": "All planned work is complete."}, "usage": {}})


def _driven_run(app):
    app.store.put("source", {"id": "src1", "project_id": "p", "title": "Coupon course"})
    return app.store.put("run", {"kind": "agent", "autonomous": True, "project_id": "p", "status": "running",
        "provider": "ollama", "model": "m", "prompt": "Investigate the coupon endpoint", "summary": "",
        "source_ids": ["src1"], "events": [], "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}})


def test_hook_fires_on_real_work_completion(app, monkeypatch):
    client = _PromoClient()
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _driven_run(app)

    app.runs._work(run["id"], threading.Event())

    saved = app.store.get("run", run["id"])
    assert saved["status"] == "completed"
    plan = app.store.list("plan", run_id=run["id"])[0]
    assert any(s["status"] == "done" for s in plan["subtasks"])   # the agent really marked a subtask done
    cards = app.store.list("reviewed_skill")
    assert len(cards) == 1
    assert cards[0]["status"] == "candidate" and "autodrafted" in cards[0]["tags"]
    assert cards[0]["evidence_id"] == "src1"


def test_promotion_failure_never_changes_the_run_outcome(app, monkeypatch):
    client = _PromoClient()
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    def boom(*a, **k):
        raise RuntimeError("propose exploded")
    monkeypatch.setattr(app.skills, "propose", boom)
    run = _driven_run(app)

    app.runs._work(run["id"], threading.Event())   # must not raise

    assert app.store.get("run", run["id"])["status"] == "completed"   # promotion failure is swallowed
    assert app.store.list("reviewed_skill") == []                     # and nothing was drafted
