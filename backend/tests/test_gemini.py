import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from bughunter.app import Application
from bughunter.store import Store
from bughunter.source_models import media_config,study_settings,validate
from bughunter.gemini_subscription import GeminiSession


def test_new_gemini_discovery_does_not_shadow_the_codex_connection(tmp_path,monkeypatch):
    import bughunter.provider_models as module
    monkeypatch.setattr(module,'connection_status',lambda:{'ready':True,'models':[{'id':'gpt-6.1-sol'}],'detail':'connected'})
    result=module.ProviderModels(Store(tmp_path)).discover('codex')
    assert result['ok'] and result['models'][0]['id']=='gpt-6.1-sol'


def test_gemini_model_listing_normalizes_resource_names(tmp_path,monkeypatch):
    import bughunter.provider_models as module
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def get(self,*args,**kwargs):return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'id':'models/gemini-3.8-flash'}]})
    monkeypatch.setattr(module.httpx,'Client',Client);monkeypatch.setattr(module.keyring,'get_password',lambda *_:'fixture')
    catalog=module.ProviderModels(Store(tmp_path)).discover('gemini')
    assert catalog['models'][0]['id']=='gemini-3.8-flash'
    assert catalog['models'][0]['effort']['levels']==['low','medium','high']


def test_not_signed_in_never_launches_gemini_or_a_browser(monkeypatch):
    import bughunter.gemini_subscription as module
    monkeypatch.setattr(module,'connection_status',lambda:{'ready':False,'detail':'Sign in with Google.'})
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:pytest.fail('Unexpected launch'))
    with pytest.raises(ValueError,match='Sign in'):GeminiSession('gemini-3.8-flash').__enter__()


def test_acp_uses_inline_images_and_does_not_expose_client_file_access(tmp_path,monkeypatch):
    session=GeminiSession('gemini-3.8-flash','medium');session.session_id='fixture';calls=[]
    monkeypatch.setattr(session,'call',lambda method,params:(calls.append((method,params)),session.text.append('Visible facts: red.\nNarrator says: none.'),{'stopReason':'end_turn'})[-1])
    image=tmp_path/'frame.png';Image.new('RGB',(8,8),'red').save(image)
    assert 'red' in session.run('system','text',[image])
    prompt=calls[0][1]['prompt']
    assert prompt[1]['type']=='image' and prompt[1]['mimeType']=='image/png'
    assert 'data' in prompt[1] and 'path' not in prompt[1]
    with pytest.raises(ValueError):session.run('s','p',[image]*4)


def test_acp_requests_are_denied_and_only_assistant_text_is_returned(monkeypatch):
    session=GeminiSession('gemini-3.8-flash');sent=[]
    monkeypatch.setattr(session,'send',sent.append)
    session.events.put({'id':99,'method':'fs/read_text_file','params':{'path':'private'}})
    session.events.put({'method':'session/update','params':{'update':{'sessionUpdate':'agent_thought_chunk','content':{'type':'text','text':'private reasoning'}}}})
    session.events.put({'method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'answer'}}}})
    session.events.put({'id':1,'result':{'stopReason':'end_turn'}})
    assert session.run('s','p')=='answer'
    assert sent[1]['error']['code']==-32601


def test_gemini_cancel_interrupts_an_active_wait_without_a_network_call(monkeypatch):
    def cancel():raise InterruptedError('Stop All')
    session=GeminiSession('gemini-3.8-flash',cancel_check=cancel)
    monkeypatch.setattr(session,'send',lambda *_:None)
    with pytest.raises(InterruptedError,match='Stop All'):session.call('session/prompt',{})


def test_gemini_choices_and_effort_survive_resource_and_study_routing(tmp_path):
    store=Store(tmp_path)
    profile=validate({'provider':'gemini_subscription','model':'gemini-3.8-flash','effort':'low'},store)
    source={'id':'source','analysis_model':profile}
    config=media_config({'vision_provider':'local','claude_parallel_enabled':True},source)
    assert config['gemini_vision_model']=='gemini-3.8-flash' and config['vision_effort']=='low'
    assert config['hosted_vision_consent'] and not config['claude_parallel_enabled']
    settings=study_settings({'provider':'codex','model':'gpt-6.1-sol','api_base_url':'https://unrelated.test'},source,store)
    assert settings['provider']=='gemini_cli' and settings['api_base_url']=='' and settings['reasoning_effort']=='low'
    with pytest.raises(ValueError):validate({**profile,'effort':'max'},store)


@pytest.mark.parametrize('provider',['gemini','openai','anthropic'])
def test_api_images_use_saved_connection_key_model_and_effort(tmp_path,monkeypatch,provider):
    import bughunter.vision_api as module
    store=Store(tmp_path)
    store.set_setting('provider_connections',{provider:{'provider':provider,'pricing_confirmed':True,'api_budget_usd':1,'input_price_per_million':1,'output_price_per_million':1}})
    image=tmp_path/'frame.png';Image.new('RGB',(8,8),'red').save(image)
    captured=[]
    class Client:
        def __init__(self,**kwargs):assert not kwargs['trust_env'] and not kwargs['follow_redirects']
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def post(self,url,**kwargs):
            captured.append((url,kwargs));return SimpleNamespace(is_error=False,json=lambda:{'choices':[{'message':{'content':'Visible facts: red.\nNarrator says: none.'}}],'content':[{'type':'text','text':'Visible facts: red.\nNarrator says: none.'}]})
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    monkeypatch.setattr(module.keyring,'get_password',lambda *_:'private-test-key')
    model='gemini-3.8-flash' if provider=='gemini' else 'gpt-6.1-sol' if provider=='openai' else 'claude-opus-4-7'
    text=module.describe(store,{'vision_provider':provider,'api_vision_model':model,'vision_effort':'low'},'evidence',[image],lambda:None)
    assert text.startswith('Visible facts')
    payload=captured[0][1]['json'];assert payload['model']==model
    assert (payload.get('output_config',{}).get('effort') or payload.get('reasoning_effort'))=='low'
    assert 'tools' not in payload
    assert 'private-test-key' not in json.dumps(store.setting('media_api_usage:image-tests:'+provider))


def test_api_pricing_and_budget_are_checked_before_sending_any_images(tmp_path,monkeypatch):
    import bughunter.vision_api as module
    store=Store(tmp_path);image=tmp_path/'frame.png';Image.new('RGB',(8,8),'red').save(image)
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:pytest.fail('Unexpected inference'))
    config={'vision_provider':'gemini','api_vision_model':'gemini-3.8-flash'}
    with pytest.raises(ValueError,match='prices'):module.describe(store,config,'prompt',[image],lambda:None)
    store.set_setting('provider_connections',{'gemini':{'pricing_confirmed':True,'api_budget_usd':0,'input_price_per_million':1}})
    monkeypatch.setattr(module.keyring,'get_password',lambda *_:'private')
    with pytest.raises(ValueError,match='budget'):module.describe(store,config,'prompt',[image],lambda:None)
