"""The chat agent drives Burp only for its own project, with operator-controlled authorization."""
import pytest

from bughunter.app import Application
from bughunter.harness_modes import require_tool


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path, primary=False)
    seen = []
    monkeypatch.setattr(a, 'dispatch', lambda method, params: seen.append((method, dict(params))) or {'ok': True})
    a.seen = seen
    yield a
    a.close()


def test_catalog_lists_burp_actions(app):
    methods = {row['method'] for row in app.harness.actions.catalog(query='burp', limit=50)['items']}
    assert {'burp.health', 'burp.web.review', 'burp.issues', 'burp.repeater.open', 'burp.audit.start', 'burp.audit.stop'} <= methods


def test_project_is_forced_and_approval_comes_from_settings(app):
    app.harness.actions.call('burp.web.review', {'project_id': 'someone_else'}, project_id='p_task')
    assert app.seen[-1] == ('burp.web.review', {'project_id': 'p_task'})
    app.harness.actions.call('burp.audit.start', {'history_id': 7, 'approved': False}, project_id='p_task')
    assert app.seen[-1][1] == {'history_id': 7, 'approved': True, 'project_id': 'p_task'}
    app.store.set_setting('autonomy_mode', False)
    with pytest.raises(PermissionError, match='Autonomous requests are off'):
        app.harness.actions.call('burp.audit.start', {'history_id': 7, 'approved': True}, project_id='p_task')
    app.harness.actions.call('burp.audit.stop', {}, project_id='p_task')
    assert app.seen[-1][1]['approved'] is True


def test_plan_mode_reads_but_never_starts_audits():
    plan = {'execution_mode': 'plan'}
    require_tool(plan, 'app_action', {'method': 'burp.web.review'})
    with pytest.raises(PermissionError):
        require_tool(plan, 'app_action', {'method': 'burp.audit.start'})
