"""Autonomy within the approval gate, and durable resume of long autonomous runs.

Two guarantees are verified here against the real RunManager._api loop and the real
ModelTools._autodispatch, with only the provider HTTP transport stubbed:

1. An autonomous run auto-SENDS only policy-classified read-only requests. A
   state-changing request is left queued for the operator (AGENTS.md invariant 1);
   the model never approves its own writes.
2. A run persists a bounded transcript each turn and, when resumed, continues from
   that transcript and its turn counter instead of restarting from the prompt.
"""
import threading
import pytest

from bughunter.app import Application
from bughunter.model_tools import ModelTools
from bughunter.providers import (DEFAULT_SETTINGS, storable_transcript,
                                 relevant_methods, _truncate_strings)
import bughunter.providers as providers


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


# ---- pure helpers -----------------------------------------------------------

def test_truncate_strings_bounds_content_but_keeps_structure():
    msg = [{"role": "tool", "content": "x" * 10000, "tool_call_id": "c1"},
           {"role": "assistant", "content": [{"type": "text", "text": "y" * 9000}]}]
    out = storable_transcript(msg)
    assert out[0]["tool_call_id"] == "c1"              # structure/pairing preserved
    assert len(out[0]["content"]) < 4200               # content bounded
    assert out[1]["content"][0]["type"] == "text"
    assert "truncated for storage" in out[0]["content"]
    assert _truncate_strings(42) == 42 and _truncate_strings("ok") == "ok"


def test_relevant_methods_returns_only_reviewed_methods(app):
    app.store.put("project", {"id": "p", "name": "x", "scope": ["https://e.test"], "authorization": "ok"})
    src = app.store.put("source", {"project_id": "p", "title": "s"})
    # A candidate (unreviewed) method must NOT surface.
    app.skills.propose(title="Coupon stacking probe", method="Try stacking two coupon codes on one order and compare the server total.", evidence_id=src["id"], project_id="p", tags=["coupon"])
    assert relevant_methods(app, "check coupon stacking", "p") == ""
    # Once verified by a human, it is eligible for auto-recall.
    row = app.store.list("reviewed_skill")[0]
    app.skills.review(id=row["id"], verified=True, note="Reproduced in the disposable lab shop.")
    primed = relevant_methods(app, "check coupon stacking on checkout", "p")
    assert "Coupon stacking probe" in primed and "learned earlier" in primed


# ---- the approval gate ------------------------------------------------------

def _autorun(app, **kw):
    return app.store.put("run", {"kind": "agent", "autonomous": True, "project_id": "p",
        "status": "running", "provider": "ollama", "model": "m", "prompt": "go", "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}, **kw})


def test_state_changing_request_is_queued_not_auto_approved(app, monkeypatch):
    run = _autorun(app)
    tools = ModelTools(app, run["id"], study=False)
    # If the model ever tried to approve its own write, that is the invariant breach.
    monkeypatch.setattr(app.policy, "approve", lambda *a, **k: pytest.fail("autonomous run approved its own state-changing request"))
    sent = []
    monkeypatch.setattr(app.policy, "execute", lambda i: sent.append(i))
    out = tools._autodispatch({"id": "a1", "effect": "state_changing", "status": "pending_approval"})
    assert out["autonomous"] is False and out["awaiting_approval"] is True
    assert "NOT sent" in out["note"]
    assert sent == []                                   # nothing was transmitted


def test_read_only_request_is_auto_sent(app, monkeypatch):
    run = _autorun(app)
    tools = ModelTools(app, run["id"], study=False)
    monkeypatch.setattr(app.policy, "execute", lambda i: {"id": i, "status": "completed", "response": {"status": 200}})
    out = tools._autodispatch({"id": "a2", "effect": "read_only", "status": "ready"})
    assert out["autonomous"] is True and out["status"] == "completed"


# ---- durable transcript + resume against the real _api loop -----------------

class _Resp:
    def __init__(self, payload): self._p, self.status_code = payload, 200
    def raise_for_status(self): pass
    def json(self): return self._p


class _Client:
    """Minimal httpx.Client stand-in for the Ollama code path."""
    def __init__(self, *a, **k): self.seen = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def post(self, url, json=None, headers=None):
        if url.endswith("/api/show"):
            return _Resp({"capabilities": ["tools"]})       # native tool API available
        self.seen.append(json)                              # /api/chat body
        return _Resp({"message": {"role": "assistant", "content": "Finished; nothing to send."}, "usage": {}})


def test_api_loop_persists_transcript_then_resumes_from_it(app, monkeypatch):
    captured = {}
    def factory(*a, **k):
        c = _Client(); captured["client"] = c; return c
    monkeypatch.setattr(providers.httpx, "Client", factory)

    run = _autorun(app, status="running")
    answer = app.runs._api(run, threading.Event())
    assert "Finished" in answer
    saved = app.store.get("run", run["id"])
    assert isinstance(saved.get("transcript"), list) and saved["transcript"]
    assert saved.get("turns_done") == 0                 # one model call => turn index 0

    # Resume: plant a sentinel transcript + turn counter and confirm the loop reloads
    # it (rather than rebuilding from the prompt) and continues the turn budget.
    sentinel = [{"role": "user", "content": "EARLIER-CONTEXT-SENTINEL"},
                {"role": "assistant", "content": "earlier partial progress"}]
    resumed = app.store.put("run", {**saved, "status": "running", "resuming": True,
        "transcript": sentinel, "turns_done": 1})
    app.runs._api(resumed, threading.Event())
    first_body = captured["client"].seen[0]
    contents = "".join(str(m.get("content", "")) for m in first_body["messages"])
    assert "EARLIER-CONTEXT-SENTINEL" in contents        # reloaded prior transcript
    assert "operator has reviewed and sent" in contents  # continuation nudge appended
    # The body prepends the system prompt; the first *conversation* message must be the
    # reloaded sentinel, proving the run did not rebuild a fresh "Current project:" turn.
    convo = [m for m in first_body["messages"] if m.get("role") != "system"]
    assert convo[0]["content"] == "EARLIER-CONTEXT-SENTINEL"
    assert not any("Current project:" in str(m.get("content", "")) for m in convo)
