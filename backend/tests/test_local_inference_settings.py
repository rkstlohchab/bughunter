import httpx
import pytest
from bughunter.store import Store
from bughunter.providers import ProviderSettings
from bughunter.model_routing import request_chat


def test_local_chat_uses_bounded_memory_and_output_settings(tmp_path, monkeypatch):
    settings = ProviderSettings(Store(tmp_path)).save({'provider': 'ollama', 'model': 'fixture',
        'ollama_context_tokens': 4096, 'ollama_max_output_tokens': 256,
        'ollama_thinking': False, 'ollama_timeout_seconds': 240})
    seen = {}
    class Client:
        def __init__(self, **kwargs): seen.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            seen.update(kwargs); seen['url'] = url
            return httpx.Response(200, json={'message': {'content': 'Local answer'}}, request=httpx.Request('POST', url))
    monkeypatch.setattr('bughunter.model_routing.httpx.Client', Client)
    result = request_chat({'id': 'primary', **settings}, [{'role': 'user', 'content': 'Hello'}], 'Local research assistant', 512, 100)
    assert result == 'Local answer'
    assert seen['url'] == 'http://127.0.0.1:11434/api/chat'
    assert seen['json']['options'] == {'num_ctx': 4096, 'num_predict': 256}
    assert seen['json']['think'] is False
    assert seen['timeout'] == 240 and seen['trust_env'] is False


@pytest.mark.parametrize('changes', [
    {'ollama_context_tokens': 0}, {'ollama_max_output_tokens': 1000000},
    {'ollama_timeout_seconds': 1000000}, {'ollama_thinking': 'false'},
])
def test_invalid_local_settings_do_not_replace_saved_connection(tmp_path, changes):
    service = ProviderSettings(Store(tmp_path))
    service.save({'provider': 'ollama', 'model': 'fixture'})
    with pytest.raises(ValueError): service.save(changes)
    assert service.get()['model'] == 'fixture'
