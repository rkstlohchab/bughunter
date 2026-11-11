"""Regressions for the simplified UI's backend capabilities."""
from __future__ import annotations

import types
import sys

import httpx
import pytest

from bughunter.store import Store
from bughunter.providers import ProviderSettings, probe_provider, SELF_HOSTED_PROVIDERS
from bughunter.knowledge import KnowledgeService


def test_api_prices_are_optional_for_self_hosted_provider(tmp_path, monkeypatch):
    from bughunter import providers
    keys = {('BugHunterClaudex','openai_compatible'): 'hidden'}
    monkeypatch.setattr(providers.keyring, 'get_password', lambda s,p: keys.get((s,p)))
    def models(request):
        assert request.method == 'GET' and request.url.path == '/v1/models'
        return httpx.Response(200, json={'data':[{'id':'bughunter-moe'}]})
    client = httpx.Client
    monkeypatch.setattr(providers.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(models), **kwargs))
    settings = ProviderSettings(Store(tmp_path / 'db'))
    stored = settings.save({'provider':'openai_compatible','model':'bughunter-moe',
                            'api_base_url':'https://example.test/v1','pricing_confirmed':False,
                            'api_budget_usd':0})
    assert stored['pricing_confirmed'] is False
    assert probe_provider(stored)['ok'] is True
    assert 'openai_compatible' in SELF_HOSTED_PROVIDERS
    assert settings.credentials()['openai_compatible'] is True
    assert 'hidden' not in str(settings.credentials())


def test_paid_api_still_requires_price_confirmation(tmp_path, monkeypatch):
    from bughunter import providers
    monkeypatch.setattr(providers.keyring, 'get_password', lambda *args: 'hidden')
    settings = ProviderSettings(Store(tmp_path / 'db'))
    stored = settings.save({'provider':'openai','model':'gpt-test', 'pricing_confirmed':False})
    assert probe_provider(stored)['ok'] is False
    assert 'confirmed pricing' in probe_provider(stored)['detail']


def test_public_youtube_playlist_is_queued_as_separate_sources(tmp_path, monkeypatch):
    class FakeDownloader:
        def __init__(self, options):
            assert options['playlistend'] == 50
            assert options['skip_download'] is True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            assert download is False
            return {'title': 'My public course', 'entries':[
                {'id':'aBcDeFgHiJ1'}, {'id':'aBcDeFgHiJ2'}, {'id':'aBcDeFgHiJ1'},
                {'id':'bad'}]}
    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=FakeDownloader))
    service = KnowledgeService(Store(tmp_path / 'db'), start_worker=False)
    try:
        imported = service.import_source(url='https://www.youtube.com/playlist?list=PLTest456')
        assert imported['count'] == 2
        assert len(imported['source_ids']) == 2
        assert all(s['status'] == 'queued' for s in imported['sources'])
        assert not service.store.list('action')
    finally:
        service.shutdown()


def test_specialist_chat_retrieves_only_reviewed_notes(tmp_path):
    from bughunter.chat import ChatService
    from bughunter.store import Store
    class Settings:
        def get(self): return {"provider":"openai_compatible","model":"bughunter-moe"}
    store = Store(tmp_path / 'db')
    store.put('training_example', {'status':'draft', 'instruction':'Cheat Engine scan',
                                   'response':'Private unrevised draft', 'domain':'cheat_engine'})
    store.put('training_example', {'status':'reviewed','instruction':'Cheat Engine scan',
                                   'response':'Use a refined scan in the local test program', 'domain':'cheat_engine'})
    store.put('tor_page', {'reviewed':False,'title':'Tor forum', 'text':'Private unreviewed scan'})
    svc = ChatService(store, Settings())
    notes = svc._specialist_notes('Cheat Engine scan')
    assert 'refined scan' in notes
    assert 'Private unrevised draft' not in notes
    assert 'Private unreviewed scan' not in notes


def test_workspace_only_project_still_blocks_unspecified_web_target(tmp_path):
    from bughunter.policy import PolicyService
    store = Store(tmp_path / 'db')
    policy = PolicyService(store)
    project = policy.save_project({'name':'Local AI research','scope':[], 'workspace_only':True})
    assert project['workspace_only'] is True
    assert project['scope'] == []
    with pytest.raises(ValueError):
        policy.queue(project['id'], {'method':'GET','url':'https://example.com/', 'headers':{},'body':''})
    with pytest.raises(ValueError, match='explicit target URL'):
        policy.save_project({'name':'Not a workspace-only project','scope':[]})
