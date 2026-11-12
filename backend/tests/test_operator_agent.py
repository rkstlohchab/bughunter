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


def test_operator_mode_defaults_on_in_autonomy(app):
    """Autonomy mode is on by default → operator mode is on by default."""
    assert app.operator.enabled() is True


def test_operator_start_no_approval_needed(app, monkeypatch):
    """Autonomy: operator.start runs without approved=True."""
    import bughunter.operator_agent as oa
    monkeypatch.setattr(oa.OperatorAgent, "_loop", lambda *a, **k: None)
    run = app.operator.start(prompt="scan 127.0.0.1")
    assert run["status"] == "running"
    assert run["kind"] == "operator"


def test_operator_disable_still_works(app):
    app.operator.disable()
    assert app.operator.enabled() is False
    app.operator.enable()
    assert app.operator.enabled() is True


def test_autonomy_mode_flag_controls_auto_approval(app):
    """Flip autonomy off → _is_auto_approved returns False for non-allow-list."""
    from bughunter import autonomy
    assert autonomy.enabled(app.store) is True
    assert app.operator._is_auto_approved("shell", {"command": "id"}) is True
    app.store.set_setting("autonomy_mode", False)
    assert autonomy.enabled(app.store) is False
    assert app.operator._is_auto_approved("shell", {"command": "id"}) is False
    # restore for other tests
    app.store.set_setting("autonomy_mode", True)


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
