"""BugHunter Burp MCP: fixed project, operator-controlled authorization."""
import pytest

from bughunter.app import Application
from bughunter.burp_mcp import SPECS, BurpCapability


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path, primary=False)
    a.policy.save_project({'id': 'p', 'name': 'Owned', 'scope': ['https://shop.example'], 'authorization': 'Owner approved'})
    calls = []
    monkeypatch.setattr(a.burp_history, 'dispatch', lambda m, p: calls.append((m, dict(p))) or {'ok': True})
    a.calls = calls
    yield a
    a.close()


def test_every_tool_is_bound_to_the_launch_project(app):
    cap = BurpCapability(app, 'p')
    for name in ('burp_review', 'burp_history', 'burp_issues'):
        cap.call(name, {'project_id': 'other'})
        assert app.calls[-1][1] == {'project_id': 'p'}
    cap.call('burp_start_audit', {'history_id': 3, 'approved': False})
    assert app.calls[-1] == ('burp.audit.start', {'project_id': 'p', 'history_id': 3, 'approved': True})


def test_active_tools_follow_autonomy_and_stop_always_works(app):
    cap = BurpCapability(app, 'p')
    app.store.set_setting('autonomy_mode', False)
    for name in ('burp_start_audit', 'burp_open_in_repeater'):
        with pytest.raises(PermissionError):
            cap.call(name, {'history_id': 3})
    cap.call('burp_stop_audit', {})
    assert app.calls[-1] == ('burp.audit.stop', {'approved': True})
    assert cap.call('burp_status', {})['autonomous_actions_allowed'] is False


def test_unknown_project_is_refused(app):
    with pytest.raises(ValueError):
        BurpCapability(app, 'missing')


def test_specs_are_valid_mcp_tools():
    assert {s['name'] for s in SPECS} == {'burp_status', 'burp_review', 'burp_history', 'burp_issues', 'burp_open_in_repeater', 'burp_start_audit', 'burp_audit_status', 'burp_stop_audit'}
    assert all(s['parameters']['type'] == 'object' for s in SPECS)
