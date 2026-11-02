"""The UI must not demand a Brave key, and readiness must never start a tool."""
from __future__ import annotations

import importlib.util
import sys
import types

if importlib.util.find_spec('keyring') is None:
    sys.modules['keyring'] = types.SimpleNamespace(get_password=lambda *args: None,
                                                    set_password=lambda *args: None)

from bughunter.task_readiness import inspect_task
from bughunter.research import WebResearch, _clean_rows


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.events = []
    def get(self, kind, id):
        return self.rows.get((kind, id))
    def put(self, kind, row, *, id):
        self.rows[(kind, id)] = {**row, 'id': id}
    def event(self, kind, data):
        self.events.append((kind, data))


def test_free_search_does_not_require_brave_key_or_charge_it(monkeypatch):
    store = FakeStore()
    client = WebResearch(store, None)
    calls = []
    monkeypatch.setattr(client, '_free_search', lambda query: calls.append(query) or [
        {'title': 'Example', 'url': 'https://example.org/a', 'snippet': 'Public info'}])
    monkeypatch.setattr(client, '_brave_search', lambda *args: (_ for _ in ()).throw(AssertionError('Brave was called')))
    result = client.search({'query': 'example lookup'})
    assert result['engine'] == 'free'
    assert result['results'][0]['url'] == 'https://example.org/a'
    assert calls == ['example lookup']
    assert client.dispatch('research.settings', {})['requires_search_key'] is False


def test_auto_falls_back_to_wikipedia_when_free_unavailable(monkeypatch):
    client = WebResearch(FakeStore(), None)
    monkeypatch.setattr(client, '_free_search', lambda query: (_ for _ in ()).throw(RuntimeError('rate limited')))
    monkeypatch.setattr(client, '_wikipedia_search', lambda query: [
        {'title': 'Fallback', 'url': 'https://en.wikipedia.org/wiki/Fallback', 'snippet': 'Article'}])
    result = client.search({'query': 'fallback test'})
    assert result['engine'] == 'wikipedia'


def test_filter_disallows_nonpublic_and_nonhttps_results():
    entries = [
        {'title':'internal','href':'https://127.0.0.1/admin','body':'x'},
        {'title':'bad','href':'javascript:alert(1)','body':'x'},
        {'title':'local','href':'https://server.local/private','body':'x'},
        {'title':'valid','href':'https://example.org/report','body':'x'},
    ]
    rows = _clean_rows(entries, url='href', snippet='body')
    assert len(rows) == 1 and rows[0]['title'] == 'valid'


def test_website_preflight_needs_scope_and_gateway_without_executing():
    result = inspect_task('website', settings={}, dependencies=[], project=None, gateway={})
    assert result['needs_setup'] is True
    assert [s['action'] for s in result['steps'] if s['status'] == 'setup'] == ['project','connect_web']


def test_research_preflight_works_without_brave_or_burp():
    result = inspect_task('research', settings={'provider':'ollama', 'model':'local'}, credentials={}, dependencies=[])
    assert result['needs_setup'] is False
    assert any('no Brave key' in s['detail'] for s in result['steps'])


def test_memory_preflight_does_not_claim_process_attached():
    result = inspect_task('memory', settings={}, dependencies=[{'name':'macOS memory bridge','installed':True}])
    assert result['needs_setup'] is False
    assert 'select an accessible process' in result['steps'][0]['detail']


def test_unknown_task_does_not_trigger_tool_execution():
    import pytest
    with pytest.raises(ValueError, match='Unknown task'):
        inspect_task('delete_everything', settings={})
