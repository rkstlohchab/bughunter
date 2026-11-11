"""Chat keeps conversation context, task evidence and progress across restarts."""
import base64
import json
from types import SimpleNamespace

import pytest

from bughunter.app import Application
from bughunter.chat import MAX_MESSAGES
from bughunter.model_tools import ModelTools
from bughunter.providers import DEFAULT_SETTINGS
from bughunter.tasks import TaskService
from bughunter.orchestrator import Orchestrator


@pytest.fixture
def app(tmp_path, monkeypatch):
    application = Application(tmp_path, primary=False)
    application.settings.save({**DEFAULT_SETTINGS, 'provider': 'ollama', 'model': 'fixture'})
    monkeypatch.setattr(application.runs, '_preflight', lambda _: None)
    monkeypatch.setattr(application.runs, '_launch', lambda _: None)
    yield application
    application.shutdown()


def test_agent_keeps_context_and_restores_finished_result(app):
    first = app.chat.agent({'message': 'Remember project symbol ZEBRA.'})
    run = app.store.get('run', first['run_id'])
    app.store.put('run', {**run, 'status': 'completed', 'summary': 'Recorded ZEBRA.'})
    # Loading a chat finalizes its result even if the browser disappeared.
    restored = app.chat.dispatch('chat.get', {'id': first['session']['id']})
    assert restored['messages'][-1]['content'] == 'Recorded ZEBRA.'
    second = app.chat.agent({'id': restored['id'], 'message': 'What symbol did I give you?'})
    run2 = app.store.get('run', second['run_id'])
    assert 'ZEBRA' in run2['conversation_context']
    assert second['session']['task_ids'] == [run['id'], run2['id']]
    assert len(second['session']['messages']) == 4
    with pytest.raises(ValueError, match='active task'):
        app.chat.agent({'id': restored['id'], 'message': 'Duplicate submission'})


def test_full_transcript_retained_while_context_is_bounded(app, monkeypatch):
    messages = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'line {i}'} for i in range(80)]
    saved = app.store.put('chat', {'title': 'Long conversation', 'messages': messages, 'task_id': 'old_task'}, id='chat_long')
    seen = []
    monkeypatch.setattr('bughunter.chat.request_chat', lambda candidate, history, *args: seen.append(history) or 'New answer')
    result = app.chat.send({'id': saved['id'], 'message': 'Continue'})
    assert len(result['messages']) == 82
    assert result['messages'][0]['content'] == 'line 0'
    assert len(seen[0]) == MAX_MESSAGES + 2
    assert result['task_ids'] == ['old_task']
    assert result['task_id'] == 'old_task'


def test_agent_attachment_and_finalization_are_scoped(app):
    for pid in ('a', 'b'): app.store.put('project', {'name': pid}, id=pid)
    app.store.put('source', {'project_id': 'b'}, id='source_private')
    with pytest.raises(ValueError, match='unavailable'):
        app.chat.agent({'message': 'Read it', 'project_id': 'a', 'source_ids': ['source_private']})
    assert not app.store.list('run')
    first = app.chat.agent({'message': 'Hello', 'project_id': 'a'})
    other = app.chat.agent({'message': 'Hello', 'project_id': 'b'})
    with pytest.raises(ValueError, match='does not belong'):
        app.chat.finalize({'id': first['session']['id'], 'run_id': other['run_id']})


def test_agent_retrieves_only_project_and_shared_sources(app):
    for pid in ('a', 'b'): app.store.put('project', {'name': pid}, id=pid)
    sources = []
    for pid, text in [('a', 'zebracode alpha'), ('b', 'zebracode privatebeta'), (None, 'zebracode shared')]:
        source = app.knowledge.import_source(filename='notes.md', project_id=pid,
            content_base64=base64.b64encode(text.encode()).decode())
        app.knowledge.process_source(source['id']); sources.append(source)
    turn = app.chat.agent({'message': 'Explain zebracode', 'project_id': 'a'})
    context = app.store.get('run', turn['run_id'])['conversation_context']
    assert 'alpha' in context and 'shared' in context and 'privatebeta' not in context
    model = ModelTools(app, turn['run_id'])
    hits = model.call('search_knowledge', {'query': 'zebracode'})
    assert {h['source_id'] for h in hits} == {sources[0]['id'], sources[2]['id']}
    with pytest.raises(ValueError): model.call('get_source', {'id': sources[1]['id']})


def test_knowledge_check_runs_without_provider_and_is_saved(app, monkeypatch):
    monkeypatch.setattr(app.runs, 'start_agent', lambda **_: pytest.fail('Integrity must not call a model'))
    answer = app.chat.autopilot({'message': 'Check my knowledge base'})
    task = app.tasks.dispatch('tasks.get', {'id': answer['task_id']})
    assert task['result']['integrity']['status'] == 'healthy'
    assert task['status'] == 'completed'
    assert 'Knowledge check' in answer['messages'][-1]['content']
    exported = json.loads(app.chat.dispatch('chat.export', {'id': answer['id']})['content'])
    assert exported['tasks'][0]['id'] == task['id']


def test_app_checks_use_fixed_suite_and_report_actual_result(app, monkeypatch):
    calls = []
    def start(suite, approved):
        calls.append((suite, approved))
        return app.store.put('p7_run', {'suite': suite, 'status': 'running', 'events': []})
    monkeypatch.setattr(app.readiness_p7, 'start', start)
    monkeypatch.setattr(app.readiness_p7, '_suite_available', lambda suite: suite == 'all')
    answer = app.chat.autopilot({'message': 'Run app checks'})
    task = app.tasks.dispatch('tasks.get', {'id': answer['task_id']})
    assert calls == [('all', True)] and task['status'] == 'running'
    run = app.store.get('p7_run', task['linked_id'])
    app.store.put('p7_run', {**run, 'status': 'failed', 'exit_code': 1, 'detail': 'One regression failed'})
    task = app.tasks.dispatch('tasks.get', {'id': task['id']})
    assert task['status'] == 'failed' and task['result']['exit_code'] == 1


def test_secondary_workers_do_not_interrupt_active_work(app):
    task = app.store.put('work_task', {'kind': 'orchestrated', 'status': 'running', 'events': []})
    TaskService(SimpleNamespace(store=app.store, primary=False))
    Orchestrator(SimpleNamespace(store=app.store, primary=False))
    assert app.store.get('work_task', task['id'])['status'] == 'running'


def test_export_retains_events_beyond_live_tail(app):
    run = app.store.put('run', {'status': 'completed', 'project_id': None, 'events': []})
    for i in range(310): app.store.event('run.event', {'run_id': run['id'], 'type': 'tool', 'text': f'event {i}', 'time': i})
    app.store.put('chat', {'messages': [], 'task_id': run['id'], 'task_ids': [run['id']]}, id='chat_export')
    live = app.tasks.dispatch('tasks.get', {'id': run['id']})
    assert live['event_count'] == 310 and live['events_truncated']
    report = json.loads(app.chat.dispatch('chat.export', {'id': 'chat_export'})['content'])
    assert len(report['tasks'][0]['events']) == 310
    assert report['tasks'][0]['events'][0]['text'] == 'event 0'


def test_provider_switch_clears_previous_endpoint_and_price_confirmation(app):
    app.settings.save({'provider': 'openai_compatible', 'model': 'custom', 'api_base_url': 'https://fixture.invalid/v1', 'pricing_confirmed': True})
    selected = app.select_model('openai', 'fixture-model')
    assert selected['api_base_url'] == '' and not selected['pricing_confirmed']
    with pytest.raises(ValueError, match='Configure this provider'):
        app.select_model('openai_compatible', 'custom')
