"""Task-ledger recovery and external tool observations, using simulated tools."""
from types import SimpleNamespace
import pytest

from bughunter.store import Store
from bughunter.tasks import TaskService
from bughunter.tor_research import TorResearchService


def service(tmp_path):
    store = Store(tmp_path / 'app')
    tasks = TaskService(SimpleNamespace(store=store))
    return store, tasks


def test_memory_task_reports_status_without_storing_tool_inputs(tmp_path):
    store, tasks = service(tmp_path)
    start = tasks.dispatch('tasks.external.start', {'kind': 'memory_session', 'title': 'Inspect test app'})
    task_id = start['id']
    with pytest.raises(TypeError):
        tasks.dispatch('tasks.external.event', {'id': task_id, 'kind': 'tool_call',
            'name': 'scan_value', 'status': 'running', 'arguments': {'private': 'very-secret'}})
    assert tasks.dispatch('tasks.external.event', {'id': task_id, 'kind': 'tool_call',
            'name': 'scan_value', 'status': 'running'})['recorded']
    tasks.dispatch('tasks.external.event', {'id': task_id, 'kind': 'approval_required',
                                           'name': 'attach_process', 'status': 'awaiting_approval'})
    assert tasks.dispatch('tasks.get', {'id': task_id})['status'] == 'awaiting_approval'
    tasks.dispatch('tasks.external.finish', {'id': task_id, 'status': 'completed'})
    loaded = tasks.dispatch('tasks.get', {'id': task_id})
    assert loaded['status'] == 'completed'
    assert 'private' not in str(loaded) and 'very-secret' not in str(loaded)
    assert any(e.get('name') == 'scan_value' for e in loaded['events'])
    assert tasks.dispatch('tasks.external.finish', {'id': task_id, 'status': 'completed'})['status'] == 'completed'
    with pytest.raises(ValueError, match='no longer active'):
        tasks.dispatch('tasks.external.event', {'id': task_id, 'kind': 'tool_call', 'name': 'another_scan'})
    with pytest.raises(ValueError, match='cannot be replayed|Reopen'):
        tasks.dispatch('tasks.retry', {'id': task_id})


def test_memory_task_restart_preserves_partial_events_and_marks_interrupted(tmp_path):
    store, tasks = service(tmp_path)
    task_id = tasks.external_start(kind='memory_session', title='Session')['id']
    tasks.external_event(task_id, 'tool_result', 'read_memory')
    second = TaskService(SimpleNamespace(store=store))
    recovered = second.dispatch('tasks.get', {'id': task_id})
    assert recovered['status'] == 'interrupted'
    assert any(e.get('name') == 'read_memory' for e in recovered['events'])
    assert recovered['events'][-1]['kind'] == 'recovered'
    third = TaskService(SimpleNamespace(store=store))
    assert len(third.dispatch('tasks.get', {'id': task_id})['events']) == len(recovered['events'])


class SimBrowser:
    def __init__(self, service): self.running = False; self.service = service
    def status(self): return {'running': self.running}
    def config(self): return {}
    def configure(self, **kwargs): return {}
    def follow(self, index): return {'url': 'https://site.example/topic'}
    def open(self, source_id):
        self.running = True
        return {'url': 'https://site.example/path?credential=abc', 'title': 'Page', 'excerpt': 'Secret'}
    def navigate(self, url):
        if url == 'bad': raise ValueError('Navigation outside saved site')
        return {'url': url}
    def capture(self): return {'id': 'page_123', 'url': 'https://site.example/path?token=private'}
    def close(self): self.running = False; return {'closed': True}


def test_browser_work_is_logged_at_backend_boundary_and_sanitized(tmp_path):
    store, tasks = service(tmp_path)
    tor = TorResearchService(store)
    tor.task_service = tasks
    tor.browser = SimBrowser(tor)
    source = tor.add_source('https://site.example/path', 'Public research site')
    tor.dispatch('tor.browser.open', {'source_id': source['id']})
    first = [x for x in tasks.dispatch('tasks.list', {}) if x['kind'] == 'tor_browser']
    assert len(first) == 1
    task_id = first[0]['id']
    with pytest.raises(ValueError, match='already running'):
        tor.dispatch('tor.browser.open', {'source_id': source['id']})
    with pytest.raises(ValueError, match='outside saved site'):
        tor.dispatch('tor.browser.navigate', {'url': 'bad'})
    assert tasks.dispatch('tasks.get', {'id': task_id})['status'] == 'running'
    tor.dispatch('tor.browser.capture', {})
    tor.dispatch('tor.browser.close', {})
    row = tasks.dispatch('tasks.get', {'id': task_id})
    assert row['status'] == 'completed'
    assert [e['kind'] for e in row['events']] == ['open', 'open', 'navigate', 'capture', 'close']
    assert row['events'][3]['capture_id'] == 'page_123'
    assert 'credential' not in str(row) and 'token=private' not in str(row)


def test_browser_failed_open_and_crash_recovery(tmp_path):
    store, tasks = service(tmp_path)
    tor = TorResearchService(store)
    tor.task_service = tasks
    source = tor.add_source('https://site.example/path', 'Test')
    class BrokenBrowser(SimBrowser):
        def open(self, source_id): raise ConnectionError('Driver did not start')
    tor.browser = BrokenBrowser(tor)
    with pytest.raises(ConnectionError): tor.dispatch('tor.browser.open', {'source_id': source['id']})
    record = [x for x in tasks.dispatch('tasks.list', {}) if x['kind'] == 'tor_browser'][0]
    assert record['status'] == 'failed'
    tor.browser = SimBrowser(tor)
    tor.dispatch('tor.browser.open', {'source_id': source['id']})
    unfinished = [x for x in tasks.dispatch('tasks.list', {}) if x['status'] == 'running' and x['kind'] == 'tor_browser'][0]
    after = TaskService(SimpleNamespace(store=store))
    assert after.dispatch('tasks.get', {'id': unfinished['id']})['status'] == 'interrupted'
    assert after.dispatch('tasks.get', {'id': record['id']})['status'] == 'failed'
