"""actions.execute -> maybe_auto_resume: the agent continuing on its own after a human
has already approved AND sent a request, with no new approval of any kind involved.
This is additive to the approval gate (AGENTS.md invariant 1), never a substitute for it -
every case here starts from an action that a human already executed."""
import pytest

from bughunter.app import Application
from bughunter.providers import DEFAULT_SETTINGS


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def make_run(app, **overrides):
    return app.store.put("run", {"project_id": "p", "status": "awaiting_approval",
        "provider": "ollama", "model": "m", "prompt": "go", "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "test-model"}, **overrides})


def make_action(app, run_id, **overrides):
    return app.store.put("action", {"project_id": "p", "run_id": run_id, "status": "completed",
        "effect": "read_only", "request": {"url": "https://example.test"}, **overrides})


def test_auto_resume_fires_after_a_real_completed_outcome(app):
    run = make_run(app)
    action = make_action(app, run["id"])
    app.runs.maybe_auto_resume(action)
    resumed = app.store.get("run", run["id"])
    # The worker may already advance from starting to running before this read.
    assert resumed["status"] in {"starting", "running"}
    assert resumed["resuming"] is True


def test_no_run_id_does_nothing(app):
    run = make_run(app)
    app.runs.maybe_auto_resume({"status": "completed"})  # e.g. a manual one-off action
    assert app.store.get("run", run["id"])["status"] == "awaiting_approval"


def test_approved_but_not_yet_sent_is_not_a_real_outcome(app):
    """A gateway-held action's execute() marks release_requested and stays status='approved' -
    the actual send (and result) happens later via the proxy, not here."""
    run = make_run(app)
    action = make_action(app, run["id"], status="approved", release_requested=True)
    app.runs.maybe_auto_resume(action)
    assert app.store.get("run", run["id"])["status"] == "awaiting_approval"


def test_other_pending_action_on_the_same_run_blocks_auto_resume(app):
    run = make_run(app)
    action = make_action(app, run["id"])
    make_action(app, run["id"], status="pending_approval")
    app.runs.maybe_auto_resume(action)
    assert app.store.get("run", run["id"])["status"] == "awaiting_approval"


def test_setting_off_disables_auto_resume(app):
    app.settings.save({"auto_resume_after_execute": False})
    run = make_run(app)
    action = make_action(app, run["id"])
    app.runs.maybe_auto_resume(action)
    assert app.store.get("run", run["id"])["status"] == "awaiting_approval"


def test_emergency_stop_is_swallowed_not_raised(app):
    app.store.set_setting("emergency_stop", True)
    run = make_run(app)
    action = make_action(app, run["id"])
    app.runs.maybe_auto_resume(action)  # must not raise
    assert app.store.get("run", run["id"])["status"] == "awaiting_approval"


def test_run_not_awaiting_approval_is_left_alone(app):
    run = make_run(app, status="completed")
    action = make_action(app, run["id"])
    app.runs.maybe_auto_resume(action)
    assert app.store.get("run", run["id"])["status"] == "completed"


def test_dispatch_wires_execute_into_auto_resume(app, monkeypatch):
    """The actions.execute RPC path (app.dispatch) must call maybe_auto_resume with
    whatever policy.execute() returned - not just the method existing in isolation."""
    seen = []
    monkeypatch.setattr(app.runs, "maybe_auto_resume", lambda action: seen.append(action))
    monkeypatch.setattr(app.policy, "dispatch", lambda method, p: {"id": "a1", "status": "completed"})
    app.dispatch("actions.execute", {"id": "a1"})
    assert seen == [{"id": "a1", "status": "completed"}]
