"""One memory substrate: the desktop planner reuses the operator's human-verified
methods, exactly as the web agent does, with no new execution power.

The planner is primed in its SYSTEM prompt (our own trusted guidance) with methods the
operator reviewed and marked 'verified' (SkillService.match). Candidate/unreviewed methods
are never surfaced, the block is skipped when nothing matches, and the control-label privacy
boundary (observation/target text never reaches the model) is left untouched.
"""
import json
import sys
from types import ModuleType

import pytest

from bughunter.app import Application
from bughunter.desktop_planner import DesktopPlanner, relevant_desktop_methods


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    application.store.set_setting('settings', {'provider': 'ollama', 'model': 'test-model'})
    yield application
    application.shutdown()


def _capture(monkeypatch):
    """Replace model_routing.request_chat (imported lazily inside dispatch) and capture
    the exact messages handed to the model."""
    captured = []
    fake = ModuleType('bughunter.model_routing')

    def request(candidate, messages, system, output, context):
        captured.append(messages)
        return json.dumps({'action': 'done', 'value': 'Observed UI', 'reason': 'finish'})

    fake.request_chat = request
    monkeypatch.setitem(sys.modules, 'bughunter.model_routing', fake)
    return captured


def _verified_skill(app, project_id):
    """Seed one project-scoped, operator-VERIFIED reusable method and return its title."""
    app.store.put('project', {'id': project_id, 'name': 'x', 'scope': ['https://e.test'], 'authorization': 'ok'})
    src = app.store.put('source', {'project_id': project_id, 'title': 's'})
    app.skills.propose(title='Zoom panel inspection method',
                       method='Open the View menu and choose Zoom In to inspect the panel layout before clicking.',
                       evidence_id=src['id'], project_id=project_id, tags=['zoom', 'panel'])
    row = app.store.list('reviewed_skill')[0]
    app.skills.review(id=row['id'], verified=True, note='Reproduced manually in the authorized app.')
    return 'Zoom panel inspection method'


def test_verified_method_primes_planner_system_message(app, monkeypatch):
    captured = _capture(monkeypatch)
    title = _verified_skill(app, 'p')
    planner = DesktopPlanner(app)
    result = planner.dispatch('desktop.next', {
        'objective': 'inspect the panel zoom layout', 'observation': 'Button: View',
        'history': [], 'allow_inputs': True, 'project_id': 'p'})
    assert result['proposal']['action'] == 'done'
    system = captured[0][0]
    assert system['role'] == 'system'
    assert 'Reviewed methods you may reuse for this task' in system['content']
    assert title in system['content']
    assert 'choose Zoom In' in system['content']
    # Guidance belongs in the system prompt, never the untrusted user/observation payload.
    assert title not in captured[0][1]['content']


def test_candidate_method_is_never_surfaced(app, monkeypatch):
    captured = _capture(monkeypatch)
    app.store.put('project', {'id': 'p', 'name': 'x', 'scope': ['https://e.test'], 'authorization': 'ok'})
    src = app.store.put('source', {'project_id': 'p', 'title': 's'})
    # Proposed but NOT reviewed -> status 'candidate' -> ineligible for recall.
    app.skills.propose(title='Unreviewed zoom panel probe',
                       method='Candidate: open the panel and zoom before it is checked.',
                       evidence_id=src['id'], project_id='p', tags=['zoom', 'panel'])
    planner = DesktopPlanner(app)
    planner.dispatch('desktop.next', {
        'objective': 'inspect the panel zoom layout', 'observation': 'Button: View',
        'history': [], 'allow_inputs': True, 'project_id': 'p'})
    assert 'Unreviewed zoom panel probe' not in str(captured)
    assert 'Reviewed methods you may reuse for this task' not in str(captured)
    # The helper itself returns nothing for a candidate-only corpus.
    assert relevant_desktop_methods(app, 'inspect the panel zoom layout', 'p') == ''


def test_no_project_and_no_matches_still_plans_without_a_method_block(app, monkeypatch):
    captured = _capture(monkeypatch)
    planner = DesktopPlanner(app)
    # No project_id, and nothing in the method library to match.
    result = planner.dispatch('desktop.next', {
        'objective': 'inspect the panel zoom layout', 'observation': 'Button: View',
        'history': [], 'allow_inputs': True})
    assert result['proposal']['action'] == 'done'
    assert 'Reviewed methods you may reuse for this task' not in str(captured)
    assert relevant_desktop_methods(app, 'anything at all', None) == ''


def test_control_label_privacy_boundary_is_intact(app, monkeypatch):
    """Priming must not open a new path for observation/target text. A verified method is
    present AND the observation carries secret/target text; only control labels reach the
    model (mirror test_desktop_share_boundary.py)."""
    captured = _capture(monkeypatch)
    _verified_skill(app, 'p')
    planner = DesktopPlanner(app)
    result = planner.dispatch('desktop.next', {
        'objective': 'inspect the panel zoom layout',
        'observation': 'Button: View\nText: secret patient record 123-456-789\nWindow: private customer vault',
        'history': [], 'allow_inputs': True, 'project_id': 'p'})
    assert result['proposal']['action'] == 'done'
    blob = str(captured)
    # The method primed the system prompt...
    assert 'Reviewed methods you may reuse for this task' in blob
    # ...but untrusted observation/target text is still not forwarded anywhere.
    assert 'secret patient record' not in blob
    assert 'private customer vault' not in blob
