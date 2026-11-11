"""Milestone 1 unified orchestrator: a fresh autonomous run is planned into an ordered
subtask list before any tools run, the agent reads/updates that plan with run-local
tools, and the end-of-run summary reports plan progress.

These drive the REAL RunManager._api loop and the REAL ModelTools, with only the
provider HTTP transport stubbed (the _Client/_Resp pattern from
test_autonomy_gate_and_resume.py), using the local "ollama" provider so no billing or
approval path is involved. Planning never sends a target request and never approves a
write: the gateway + human approval (AGENTS.md invariant 1) are untouched here.
"""
import threading

import pytest

from bughunter.app import Application
from bughunter.model_tools import ModelTools, TOOL_SPECS, STUDY_TOOL_NAMES, PLAN_TOOL_NAMES
from bughunter.providers import DEFAULT_SETTINGS, _parse_subtask_list
import bughunter.providers as providers


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def _autorun(app, **kw):
    return app.store.put("run", {"kind": "agent", "autonomous": True, "project_id": None,
        "status": "running", "provider": "ollama", "model": "m", "prompt": "go", "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}, **kw})


# ---- provider transport stub (Ollama path) ---------------------------------

class _Resp:
    def __init__(self, payload): self._p, self.status_code = payload, 200
    def raise_for_status(self): pass
    def json(self): return self._p


class _ScriptedClient:
    """httpx.Client stand-in for the Ollama code path. The first /api/chat is the
    planner (its prompt carries the planner marker); it is answered with planner_reply.
    Every later /api/chat is answered from the scripted `turns` list (a tool call or a
    final text), defaulting to a tool-free 'done' answer once the script is exhausted."""
    def __init__(self, planner_reply, turns=None, native=True):
        self.planner_reply, self.native = planner_reply, native
        self.turns = list(turns or [])
        self.seen, self.planner_calls, self.turn_calls = [], 0, 0

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def post(self, url, json=None, headers=None):
        if url.endswith("/api/show"):
            return _Resp({"capabilities": ["tools"] if self.native else []})
        self.seen.append(json)
        content = " ".join(str(m.get("content", "")) for m in json.get("messages", []))
        if "tools" not in json and "Decompose the objective" in content:
            self.planner_calls += 1
            return _Resp({"message": {"role": "assistant", "content": self.planner_reply}, "usage": {}})
        self.turn_calls += 1
        reply = self.turns.pop(0) if self.turns else {"content": "Done; nothing to send."}
        if "tool" in reply:
            message = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": reply["tool"], "arguments": reply["arguments"]}}]}
        else:
            message = {"role": "assistant", "content": reply["content"]}
        return _Resp({"message": message, "usage": {}})


# ---- pure parser ------------------------------------------------------------

def test_parse_subtask_list_accepts_array_fence_and_object():
    assert _parse_subtask_list('["a", "b"]') == ["a", "b"]
    assert _parse_subtask_list('```json\n["a", "b", "c"]\n```') == ["a", "b", "c"]
    assert _parse_subtask_list('Here you go: {"subtasks": ["x", {"goal": "y"}]}') == ["x", "y"]
    assert _parse_subtask_list("no json here") == []
    assert _parse_subtask_list("") == []
    assert len(_parse_subtask_list('["1","2","3","4","5","6","7","8"]')) == 6  # capped at six


# ---- the tools are registered and bounded ----------------------------------

def test_plan_tools_registered_and_excluded_from_study():
    names = {t["name"] for t in TOOL_SPECS}
    assert PLAN_TOOL_NAMES <= names
    assert not (PLAN_TOOL_NAMES & STUDY_TOOL_NAMES)   # study runs never see them


# ---- planner decomposes the objective into a stored plan -------------------

def test_planner_decomposes_objective_into_stored_plan(app, monkeypatch):
    client = _ScriptedClient('["Map the API surface", "Probe auth endpoints", "Draft a finding"]')
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _autorun(app)
    app.runs._api(run, threading.Event())

    plans = app.store.list("plan", run_id=run["id"])
    assert len(plans) == 1
    plan = plans[0]
    assert [s["goal"] for s in plan["subtasks"]] == ["Map the API surface", "Probe auth endpoints", "Draft a finding"]
    assert all(s["status"] == "pending" for s in plan["subtasks"])
    assert all(s["id"] for s in plan["subtasks"])
    # The run references its plan, and planning happened exactly once, before the loop.
    assert app.store.get("run", run["id"])["plan_id"] == plan["id"]
    assert client.planner_calls == 1


# ---- get_plan / update_plan are run-local and read/write only --------------

def test_plan_tools_are_run_local_read_write_only(app, monkeypatch):
    run = _autorun(app)
    app.store.put("plan", {"run_id": run["id"], "objective": "obj",
        "subtasks": [{"id": "s1", "goal": "Recon the API", "status": "pending"},
                     {"id": "s2", "goal": "Attempt a write", "status": "pending"}]})
    tools = ModelTools(app, run["id"], study=False)
    # If a plan tool ever reached the provider or the gateway, these would fire.
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: pytest.fail("plan tools must not send HTTP"))

    view = tools.call("get_plan", {})
    assert [s["id"] for s in view["subtasks"]] == ["s1", "s2"]

    done = tools.call("update_plan", {"subtask_id": "s1", "status": "done"})
    assert next(s for s in done["subtasks"] if s["id"] == "s1")["status"] == "done"
    blocked = tools.call("update_plan", {"subtask_id": "s2", "status": "blocked", "note": "write awaits approval"})
    assert next(s for s in blocked["subtasks"] if s["id"] == "s2")["status"] == "blocked"
    assert "instruction" in blocked and "alternative" in blocked["instruction"].lower()

    # The change persisted to the plan entity...
    reloaded = app.store.list("plan", run_id=run["id"])[0]
    assert {s["id"]: s["status"] for s in reloaded["subtasks"]} == {"s1": "done", "s2": "blocked"}
    assert next(s for s in reloaded["subtasks"] if s["id"] == "s2")["note"] == "write awaits approval"
    # ...and no gateway/approval entity was created (no action, no traffic).
    assert app.store.list("action") == [] and app.store.list("traffic") == []


def test_update_plan_rejects_bad_status_and_unknown_subtask(app):
    run = _autorun(app)
    app.store.put("plan", {"run_id": run["id"], "objective": "obj",
        "subtasks": [{"id": "s1", "goal": "Recon", "status": "pending"}]})
    tools = ModelTools(app, run["id"], study=False)
    with pytest.raises(ValueError, match="active.*done.*blocked|status"):
        tools.call("update_plan", {"subtask_id": "s1", "status": "pending"})   # not a settable status
    with pytest.raises(ValueError, match="No subtask"):
        tools.call("update_plan", {"subtask_id": "nope", "status": "done"})


# ---- planner failure falls back to a single-subtask plan -------------------

def test_planner_failure_falls_back_to_single_subtask(app, monkeypatch):
    client = _ScriptedClient("I will not be giving you any JSON today, sorry.")
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _autorun(app, prompt="Investigate the coupon endpoint for IDOR")
    answer = app.runs._api(run, threading.Event())

    plans = app.store.list("plan", run_id=run["id"])
    assert len(plans) == 1 and len(plans[0]["subtasks"]) == 1
    assert "coupon endpoint" in plans[0]["subtasks"][0]["goal"]
    assert plans[0]["subtasks"][0]["status"] == "pending"
    assert "Done" in answer                           # the run proceeded despite planning failing


def test_planner_transport_error_still_yields_a_plan(app, monkeypatch):
    class _BoomClient(_ScriptedClient):
        def post(self, url, json=None, headers=None):
            if url.endswith("/api/show"):
                return _Resp({"capabilities": ["tools"]})
            content = " ".join(str(m.get("content", "")) for m in json.get("messages", []))
            if "tools" not in json and "Decompose the objective" in content:
                raise RuntimeError("planner endpoint exploded")
            return _Resp({"message": {"role": "assistant", "content": "Done."}, "usage": {}})
    client = _BoomClient("unused")
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _autorun(app, prompt="Enumerate the staging hosts")
    answer = app.runs._api(run, threading.Event())
    plans = app.store.list("plan", run_id=run["id"])
    assert len(plans) == 1 and len(plans[0]["subtasks"]) == 1   # fell back, did not abort
    assert "staging hosts" in plans[0]["subtasks"][0]["goal"]
    assert "Done" in answer


# ---- end-of-run summary carries a deterministic plan-status line ------------

def test_end_of_run_summary_includes_plan_status(app, monkeypatch):
    client = _ScriptedClient('["Enumerate endpoints", "Attempt the write", "Summarize"]')
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _autorun(app)
    answer = app.runs._api(run, threading.Event())
    assert "Plan status: 0/3 subtasks done." in answer

    # The line is computed straight from the plan entity (no extra model call), so marking
    # outcomes changes it deterministically.
    plan = app.store.list("plan", run_id=run["id"])[0]
    ids = [s["id"] for s in plan["subtasks"]]
    tools = ModelTools(app, run["id"], study=False)       # run is still 'running' after _api returned
    tools.call("update_plan", {"subtask_id": ids[0], "status": "done"})
    tools.call("update_plan", {"subtask_id": ids[1], "status": "blocked"})
    line = app.runs._plan_status_line(app.store.get("run", run["id"]))
    assert "Plan status: 1/3 subtasks done, 1 blocked:" in line
    assert "Attempt the write" in line


# ---- resume keeps the existing plan; it does not re-plan --------------------

def test_resumed_run_does_not_create_a_second_plan(app, monkeypatch):
    client = _ScriptedClient('["One", "Two"]')
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    run = _autorun(app)
    app.runs._api(run, threading.Event())
    plans = app.store.list("plan", run_id=run["id"])
    assert len(plans) == 1 and client.planner_calls == 1
    first_plan_id = plans[0]["id"]

    # Resume with a prior transcript present: the planner must NOT run again.
    saved = app.store.get("run", run["id"])
    resumed = app.store.put("run", {**saved, "status": "running", "resuming": True,
        "transcript": [{"role": "user", "content": "earlier context"},
                       {"role": "assistant", "content": "earlier progress"}],
        "turns_done": 1})
    resume_client = _ScriptedClient('["Three", "Four", "Five"]')
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: resume_client)
    app.runs._api(resumed, threading.Event())

    plans = app.store.list("plan", run_id=run["id"])
    assert len(plans) == 1 and plans[0]["id"] == first_plan_id   # no second plan
    assert resume_client.planner_calls == 0                       # resume did not re-plan
    assert resume_client.turn_calls >= 1                          # but the loop did run


# ---- study and investigation runs are unaffected ---------------------------

def test_study_run_blocks_plan_tools(app):
    run = app.store.put("run", {"kind": "study", "source_id": "src", "project_id": None, "status": "running",
        "provider": "ollama", "model": "m", "prompt": "Study src", "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}})
    tools = ModelTools(app, run["id"], study=True)
    with pytest.raises(ValueError, match="library tools"):
        tools.call("get_plan", {})
    with pytest.raises(ValueError, match="library tools"):
        tools.call("update_plan", {"subtask_id": "x", "status": "done"})


def test_investigation_run_is_not_planned(app, monkeypatch):
    client = _ScriptedClient('["should not be requested"]')
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: client)
    app.policy.save_project({"id": "p", "name": "x", "scope": ["https://e.test"], "authorization": "ok"})
    run = app.store.put("run", {"project_id": "p", "status": "running", "provider": "ollama", "model": "m",
        "prompt": "Investigate", "events": [], "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}})
    answer = app.runs._api(run, threading.Event())
    assert app.store.list("plan", run_id=run["id"]) == []   # non-agent run: no plan
    assert client.planner_calls == 0
    assert "Plan status" not in answer
