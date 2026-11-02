"""Production boundaries: opt-in failover, key separation, static search and sandbox denial."""
import json
from unittest.mock import Mock

import httpx
import pytest

from bughunter.model_routing import ModelRoutes, request_chat, validate_route, transient
from bughunter.research import WebResearch
from bughunter.sandbox import command, run, status
from bughunter.store import Store


def fallback(provider='openai_compatible', **changes):
    return {'id':'backup1','provider':provider,'model':'sample-model',
            'base_url':'https://gpu.example/v1','enabled':True,**changes}


def test_model_routes_are_opt_in_and_validated(tmp_path, monkeypatch):
    store=Store(tmp_path)
    service=ModelRoutes(store)
    with pytest.raises(ValueError,match='HTTPS'):
        validate_route(fallback(base_url='http://gpu.example/v1'))
    with pytest.raises(ValueError,match='pricing'):
        validate_route(fallback(provider='openai',base_url='https://api.openai.com/v1'))
    saved=service.dispatch('models.routes.save',{'enabled':True,'routes':[fallback()]})
    assert len(saved['routes'])==1
    primary={'provider':'openai_compatible','model':'primary','api_base_url':'https://primary.example/v1'}
    assert [r['id'] for r in service.candidates(primary)]==['primary']
    assert [r['id'] for r in service.candidates(primary,allow_fallback=True)]==['primary','backup1']
    primary['local_only']=True
    assert [r['id'] for r in service.candidates(primary,allow_fallback=True)]==['primary']


def test_fallback_only_on_transient_errors():
    request=httpx.Request('POST','https://api.example/v1/chat/completions')
    for code in (429,500,503):
        assert transient(httpx.HTTPStatusError('error',request=request,response=httpx.Response(code,request=request)))
    for code in (400,401,403):
        assert not transient(httpx.HTTPStatusError('error',request=request,response=httpx.Response(code,request=request)))
    assert transient(httpx.ConnectError('offline'))
    assert not transient(ValueError('bad model'))


def test_routed_chat_uses_distinct_keychain_name(monkeypatch):
    from bughunter import model_routing as mr
    seen={}
    monkeypatch.setattr(mr.keyring,'get_password',lambda service,username:seen.setdefault('credential',(service,username)) or 'secret')
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            seen['url']=url;seen['body']=json
            r=Mock();r.json.return_value={'choices':[{'message':{'content':'Ready'}}]};return r
    monkeypatch.setattr(mr.httpx,'Client',Client)
    monkeypatch.setattr(mr.keyring,'get_password',lambda *args: 'test-secret' if args==('BugHunterClaudex-ModelRoutes','backup1') else None)
    text=request_chat(fallback(),[{'role':'system','content':'Guardrails'},{'role':'user','content':'hello'}], 'Guardrails',128,100)
    assert text=='Ready' and seen['url']=='https://gpu.example/v1/chat/completions'
    assert seen['body']['messages'][1]['content']=='hello'


def test_research_search_and_local_import(tmp_path,monkeypatch):
    from bughunter import research as research_module
    class K:
        def import_source(self, **kwargs):
            assert kwargs['filename'].startswith('web-research-')
            assert 'https://example.org' in __import__('base64').b64decode(kwargs['content_base64']).decode()
            return {'id':'source_123'}
    store=Store(tmp_path)
    service=WebResearch(store,K())
    monkeypatch.setattr(research_module.keyring,'get_password',lambda *args: 'secret')
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params,headers):
            assert url==research_module.BRAVE
            assert headers['X-Subscription-Token']=='secret'
            r=Mock();r.json.return_value={'web':{'results':[{'title':'An article','url':'https://example.org','description':'An <b>interesting</b> result.'}]}}
            return r
    monkeypatch.setattr(research_module.httpx,'Client',Client)
    found=service.search({'query':'test','engine':'brave'})
    assert found['results'][0]['snippet']=='An interesting result.'
    assert service.save({'id':found['id'],'index':0})['id']=='source_123'
    with pytest.raises(ValueError):service.save({'id':found['id'],'index':999})


def test_sandbox_fails_closed_and_requires_approval(tmp_path,monkeypatch):
    import bughunter.sandbox as sandbox
    with pytest.raises(ValueError,match='approval'):
        sandbox.run(tmp_path,'print("hi")',False)
    monkeypatch.setattr(sandbox.platform,'system',lambda:'Windows')
    with pytest.raises(ValueError,match='No verified OS sandbox'):
        command(tmp_path,'print("hi")')
    monkeypatch.setattr(sandbox.platform,'system',lambda:'Linux')
    monkeypatch.setattr(sandbox.shutil,'which',lambda _:None)
    with pytest.raises(ValueError,match='bubblewrap'):
        command(tmp_path,'print("hi")')
    with pytest.raises(ValueError,match='16,000'):
        command(tmp_path,'x'*16001)


def test_chat_opt_in_failover_survives_primary_timeout(tmp_path,monkeypatch):
    from bughunter.chat import ChatService
    import bughunter.chat as chat_module
    store=Store(tmp_path)
    routes=ModelRoutes(store)
    routes.dispatch('models.routes.save',{'enabled':True,'routes':[fallback()]})
    class Settings:
        def get(self):
            return {'provider':'openai_compatible','model':'first','api_base_url':'https://first.example/v1',
                    'pricing_confirmed':False,'input_price_per_million':0,'output_price_per_million':0,
                    'api_budget_usd':0,'local_only':False,'custom_system_prompt':'','provider_system_prompts':{}}
    monkeypatch.setattr(chat_module.keyring,'get_password',lambda *args: 'primary-key')
    attempts=[]
    def request(candidate, *args):
        attempts.append(candidate['id'])
        if candidate['id']=='primary':raise httpx.ConnectError('primary offline')
        return 'Fallback completed.'
    monkeypatch.setattr(chat_module,'request_chat',request)
    instance=ChatService(store,Settings(),model_routes=routes)
    with pytest.raises(ValueError,match='No configured model could answer'):
        instance.send({'message':'test'})
    assert attempts==['primary']
    attempts.clear()
    answer=instance.send({'message':'test again','allow_fallback':True})
    assert attempts==['primary','backup1']
    assert answer['model_used']=={'provider':'openai_compatible','model':'sample-model', 'route':'backup1','fallback':True}


def test_chat_auto_web_uses_untrusted_cited_snippets(tmp_path,monkeypatch):
    from bughunter.chat import ChatService
    import bughunter.chat as chat_module
    store=Store(tmp_path)
    class Settings:
        def get(self):
            return {'provider':'openai_compatible','model':'first','api_base_url':'https://first.example/v1',
                    'pricing_confirmed':False,'input_price_per_million':0,'output_price_per_million':0,
                    'api_budget_usd':0,'local_only':False,'custom_system_prompt':'','provider_system_prompts':{}}
    class Research:
        def search(self,options):
            assert options['query']=='find research'
            return {'id':'research_demo','results':[{'title':'Reference','url':'https://example.org/source','snippet':'A public reference.'}]}
    seen={}
    monkeypatch.setattr(chat_module.keyring,'get_password',lambda *args:'key')
    def request(candidate,messages,system,*args):
        seen['prompt']=system
        return 'I found a reference.'
    monkeypatch.setattr(chat_module,'request_chat',request)
    result=ChatService(store,Settings(),research=Research()).send({'message':'find research','web_search':True})
    assert result['web_research_id']=='research_demo'
    assert 'https://example.org/source' in seen['prompt']
    assert 'untrusted' in seen['prompt']


def test_macos_profile_denies_network_and_outside_workspace(tmp_path,monkeypatch):
    import bughunter.sandbox as sandbox
    monkeypatch.setattr(sandbox.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(sandbox.shutil,'which',lambda x:'/usr/bin/sandbox-exec')
    args,_=sandbox.command(tmp_path,'print(123)',python_executable='/usr/bin/python3' if __import__('pathlib').Path('/usr/bin/python3').exists() else __import__('sys').executable)
    assert args[0]=='/usr/bin/sandbox-exec'
    assert '(deny default)' in args[2]
    assert 'allow network' not in args[2]
    assert 'tmp_path' not in args[2]


def test_changing_fallback_endpoint_revokes_existing_credential(tmp_path, monkeypatch):
    from bughunter import model_routing as mr
    calls=[]
    monkeypatch.setattr(mr.keyring,'delete_password',lambda svc,key:calls.append((svc,key)))
    monkeypatch.setattr(mr.keyring,'get_password',lambda *args:None)
    routes=ModelRoutes(Store(tmp_path))
    routes.dispatch('models.routes.save',{'enabled':True,'routes':[fallback()]})
    assert not calls
    routes.dispatch('models.routes.save',{'enabled':True,'routes':[fallback(base_url='https://another.example/v1')]})
    assert calls==[(mr.SERVICE,'backup1')]
