"""OperatorAgent lifecycle + approval queue + explicit-opt-in.

The investigation path remains untouched. These tests only exercise the
new parallel surface.
"""
import pytest
from bughunter.app import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def test_operator_mode_defaults_off(app):
    assert app.operator.enabled() is False


def test_operator_start_refused_without_approval(app):
    app.operator.enable(approved=True)
    with pytest.raises(ValueError):
        app.operator.start(prompt="scan 127.0.0.1")


def test_operator_start_refused_without_mode(app):
    # mode off
    with pytest.raises(ValueError):
        app.operator.start(prompt="x", approved=True)


def test_operator_enable_then_start(app, monkeypatch):
    # neutralize the actual loop so the test doesn't block on network
    import bughunter.operator_agent as oa
    monkeypatch.setattr(oa.OperatorAgent, "_loop", lambda *a, **k: None)
    app.operator.enable(approved=True)
    assert app.operator.enabled()
    run = app.operator.start(prompt="look up 127.0.0.1",
                              objective="smoke", approved=True)
    assert run["status"] == "running"
    assert run["kind"] == "operator"
    listing = app.operator.list()
    assert any(r["id"] == run["id"] for r in listing)


def test_approvals_queue_shape(app, monkeypatch):
    import bughunter.operator_agent as oa
    monkeypatch.setattr(oa.OperatorAgent, "_loop", lambda *a, **k: None)
    app.operator.enable(approved=True)
    run = app.operator.start(prompt="x", approved=True)
    # simulate the agent queueing one call
    app.operator._queue_approval(run["id"], call_id="call1",
                                  name="shell", args={"command": "id"})
    pending = app.operator.pending_approvals()
    assert len(pending) == 1
    assert pending[0]["tool"] == "shell"


def test_tools_spec_present(app):
    spec = app.operator.tools_spec()
    names = {s["name"] for s in spec}
    assert {"invoke_adapter", "shell", "http", "file_read", "file_write",
            "code_exec", "assets_query", "vault_list"} <= names
