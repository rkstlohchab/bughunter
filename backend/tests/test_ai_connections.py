"""Provider discovery, subscription transport and saved-connection regression checks."""
import io
import json
import threading
import time

import httpx
import pytest

from bughunter.app import Application
from bughunter.codex_subscription import CodexSession, cancel_all, _ACTIVE, _LOCK
from bughunter.provider_models import ProviderModels
from bughunter.store import Store


@pytest.fixture
def app(tmp_path):
    instance = Application(tmp_path, primary=False)
    yield instance
    instance.shutdown()


@pytest.mark.parametrize('provider,url,path', [
    ('openai','', 'https://api.openai.com/v1/models'),
    ('anthropic','https://api.anthropic.com/v1','https://api.anthropic.com/v1/models'),
    ('deepseek','','https://api.deepseek.com/models'),
    ('ollama','','http://127.0.0.1:11434/api/tags'),
    ('remote_ollama','https://model.example','https://model.example/api/tags'),
    ('openai_compatible','https://model.example/v1','https://model.example/v1/models'),
    ('crucible','http://192.168.1.5:8080','http://192.168.1.5:8080/v1/models'),
])
def test_discovery_metadata_only_without_changing_active_provider(app,monkeypatch,provider,url,path):
    monkeypatch.setattr('bughunter.provider_models.keyring.get_password',lambda *a:'fixture-key')
    requests=[]
    def handle(request):
        requests.append(request)
        assert request.method=='GET'
        assert str(request.url).split('?')[0]==path
        return httpx.Response(200,json={'data':[{'id':'gpt-fixture'}], 'models':[{'name':'local-fixture'}]})
    client=httpx.Client
    monkeypatch.setattr('bughunter.provider_models.httpx.Client',lambda **kw:client(transport=httpx.MockTransport(handle),**kw))
    before=app.settings.get()
    result=app.dispatch('models.discover',{'provider':provider,'api_base_url':url})
    assert result['ok'] and len(result['models'])==1
    assert app.settings.get()==before and len(requests)==1
    assert 'fixture-key' not in json.dumps(result)


def test_missing_key_and_invalid_endpoint_never_send_request(app,monkeypatch):
    monkeypatch.setattr('bughunter.provider_models.keyring.get_password',lambda *a:None)
    monkeypatch.setattr('bughunter.provider_models.httpx.Client',lambda **kw:pytest.fail('Unexpected HTTP'))
    assert not app.provider_models.discover('openai')['ok']
    for url in ['http://remote.example/v1','https://name:password@model.example/v1','https://model.example/v1?token=secret']:
        with pytest.raises(ValueError):app.provider_models.discover('openai_compatible',url)


def test_model_list_http_error_is_not_fake_success_or_secret(app,monkeypatch):
    monkeypatch.setattr('bughunter.provider_models.keyring.get_password',lambda *a:'secret')
    client=httpx.Client
    monkeypatch.setattr('bughunter.provider_models.httpx.Client',lambda **kw:client(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='secret')),**kw))
    result=app.provider_models.discover('openai')
    assert not result['ok'] and result['models']==[] and '401' in result['detail']
    assert 'secret' not in json.dumps(result)


def test_connections_restore_endpoint_and_never_reuse_other_provider_endpoint(app):
    app.settings.save({'provider':'openai_compatible','model':'server-model','api_base_url':'https://model.example/v1'})
    app.settings.save({'provider':'ollama','model':'local-model','api_base_url':'http://localhost:11434','local_only':True})
    saved=app.select_model('openai_compatible','server-model')
    assert saved['api_base_url']=='https://model.example/v1' and not saved['local_only']
    subscription=app.select_model('codex','subscription-model')
    assert subscription['api_base_url']=='' and subscription['pricing_confirmed'] and not subscription['local_only']
    with pytest.raises(ValueError):app.settings.secret('codex','never-use-api-key')


def test_model_switch_invalidates_paid_price_confirmation(app):
    app.settings.save({'provider':'openai','model':'first','pricing_confirmed':True})
    assert not app.select_model('openai','second')['pricing_confirmed']


def session_fixture(monkeypatch, notifications, ready=True):
    session=CodexSession(timeout=1)
    sent=[]
    def call(method,params):
        if method=='account/read':return {'account':{'type':'chatgpt' if ready else 'apiKey'}}
        if method=='config/read':return {'config':{'mcp_servers':{}}}
        assert method=='thread/start'
        assert params['environments']==[] and params['sandbox']=='read-only'
        assert params['allowProviderModelFallback'] is False
        assert params['developerInstructions']==''
        return {'thread':{'id':'fixture'}}
    monkeypatch.setattr(session,'call',call)
    monkeypatch.setattr(session,'send',sent.append)
    for item in notifications:session.events.put(item)
    return session,sent


def test_subscription_runs_app_tool_and_records_final_response(monkeypatch):
    events=[{'id':81,'method':'item/tool/call','params':{'tool':'check','callId':'c1','arguments':{}}},
        {'method':'item/completed','params':{'threadId':'fixture','item':{'type':'agentMessage','text':'Done.'}}},
        {'method':'turn/completed','params':{'threadId':'fixture','turn':{'status':'completed'}}}]
    session,sent=session_fixture(monkeypatch,events)
    seen=[]
    result=session.run('model','instructions','task',[{'name':'check','description':'fixture','parameters':{}}],lambda n,a:seen.append(n) or {'ok':True})
    assert result=='Done.' and seen==['check']
    assert sent[-1]['id']==81 and sent[-1]['result']['success']


def test_subscription_rejects_api_login_unknown_tool_and_limit(monkeypatch):
    session,_=session_fixture(monkeypatch,[],ready=False)
    with pytest.raises(ValueError,match='ChatGPT account'):session.run('m','s','p')
    event={'id':9,'method':'item/tool/call','params':{'tool':'execute_shell','callId':'x'}}
    session,_=session_fixture(monkeypatch,[event])
    with pytest.raises(ValueError,match='unavailable'):session.run('m','s','p')
    event['params']['tool']='check'
    session,_=session_fixture(monkeypatch,[event])
    with pytest.raises(ValueError,match='limit'):session.run('m','s','p',[{'name':'check','parameters':{},'description':'test'}],lambda *a:pytest.fail('Tool should not execute'),max_calls=0)


def test_global_stop_interrupts_subscription_wait_and_does_not_resume():
    session=CodexSession()
    with _LOCK:_ACTIVE.add(session)
    try:
        cancel_all()
        with pytest.raises(InterruptedError):session.receive(time.monotonic()+60)
        assert session.cancel.is_set()
    finally:
        with _LOCK:_ACTIVE.discard(session)


def test_failed_full_disk_probe_is_unknown_not_denied(app,monkeypatch):
    monkeypatch.setattr('bughunter.app.platform.system',lambda:'Darwin')
    monkeypatch.setattr('builtins.open',lambda *a,**kw:(_ for _ in ()).throw(PermissionError('fixture')))
    assert app._full_disk_access() is None
