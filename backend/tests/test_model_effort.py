import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bughunter.app import Application
from bughunter.store import Store
from bughunter.provider_models import ProviderModels
from bughunter.model_effort import info,validate


def test_claude_initialize_returns_versions_and_efforts_without_an_inference_turn(tmp_path,monkeypatch):
    import bughunter.provider_models as module
    captured=[]
    def run(args,**kwargs):
        captured.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'control_response','response':{
            'subtype':'success','response':{'account':{'secret':'must not persist'},'models':[
                {'value':'opus','resolvedModel':'claude-opus-4-7','supportsEffort':True,'supportedEffortLevels':['low','medium','high','xhigh','max']},
                {'value':'sonnet','resolvedModel':'claude-sonnet-4-6','supportsEffort':True,'supportedEffortLevels':['low','medium','high','max']}]}}}))
    monkeypatch.setattr(module,'claude_status',lambda:{'subscription_ready':True})
    monkeypatch.setattr(module.shutil,'which',lambda _: '/fixture/claude')
    monkeypatch.setattr(module.subprocess,'run',run)
    store=Store(tmp_path);catalog=ProviderModels(store).discover('claude_code')
    assert catalog['ok']
    assert 'opus' not in [model['id'] for model in catalog['models']]
    opus=next(model for model in catalog['models'] if model['id']=='claude-opus-4-7')
    assert opus['label']=='Claude Opus 4.7' and opus['effort']['levels']==['low','medium','high','xhigh','max']
    request=json.loads(captured[0][1]['input']);assert request['type']=='control_request' and request['request']['subtype']=='initialize'
    assert '--tools' in captured[0][0]
    assert 'must not persist' not in json.dumps(store.setting('provider_model_catalogs'))


def test_unknown_models_do_not_inherit_another_models_effort_levels(tmp_path):
    assert info('claude_code','claude-haiku-unknown')['levels']==[]
    assert info('ollama','custom')['levels']==[]
    assert 'xhigh' not in info('claude_code','claude-sonnet-4-6')['levels']
    with pytest.raises(ValueError,match='does not report support'):validate('claude_code','claude-sonnet-4-6','xhigh',Store(tmp_path))


def test_selection_persists_effort_and_rejects_unsupported_level_atomically(tmp_path,monkeypatch):
    app=Application(tmp_path,primary=False)
    try:
        app.store.set_setting('provider_model_catalogs',{'codex':{'models':[{'id':'gpt-6.1-sol',
            'effort':{'levels':['low','medium','high','max','ultra'],'default':'low','source':'provider_catalog'}}]}})
        result=app.dispatch('models.select',{'provider':'codex','model':'gpt-6.1-sol','reasoning_effort':'ultra'})
        assert (result['provider'],result['model'],result['reasoning_effort'])==('codex','gpt-6.1-sol','ultra')
        assert app.settings.get()['reasoning_effort']=='ultra'
        app.dispatch('models.select',{'provider':'claude_code','model':'claude-sonnet-4-6','reasoning_effort':'medium'})
        before=app.settings.get()
        with pytest.raises(ValueError):app.dispatch('models.select',{'provider':'claude_code','model':'claude-sonnet-4-6','reasoning_effort':'xhigh'})
        assert app.settings.get()==before
        app.dispatch('models.select',{'provider':'ollama','model':'fixture'})
        assert app.settings.get()['reasoning_effort']==''
    finally:app.shutdown()


def test_chat_catalog_keeps_version_labels_and_provider_effort_metadata(tmp_path,monkeypatch):
    app=Application(tmp_path,primary=False)
    try:
        app.store.set_setting('settings',{'provider':'ollama','model':'fixture'})
        monkeypatch.setattr(app.media,'status',lambda:{})
        monkeypatch.setattr(app.settings,'credentials',lambda:{})
        monkeypatch.setattr('bughunter.app.claude_status',lambda:{'subscription_ready':True})
        def discover(provider):
            return {'ok':True,'models':[{'id':'claude-sonnet-4-6' if provider=='claude_code' else 'gpt-6.1-sol',
                'label':'Claude Sonnet 4.6' if provider=='claude_code' else 'GPT-6.1 Sol',
                'effort':{'levels':['low','medium','high'],'default':'medium','source':'provider_catalog'}}]}
        monkeypatch.setattr(app.provider_models,'discover',discover)
        result=app.available_models()
        claude=next(p for p in result['providers'] if p['provider']=='claude_code')
        assert claude['models']==['claude-sonnet-4-6']
        assert claude['model_options'][0]['label']=='Claude Sonnet 4.6'
        assert claude['model_options'][0]['effort']['levels']==['low','medium','high']
    finally:app.shutdown()


@pytest.mark.parametrize('provider,expected_key',[('openai','reasoning_effort'),('anthropic','output_config')])
def test_ordinary_api_chat_forwards_selected_effort(tmp_path,monkeypatch,provider,expected_key):
    import bughunter.model_routing as module
    payloads=[]
    client=Mock();response=Mock();response.json.return_value={'choices':[{'message':{'content':'answer'}}],'content':[{'type':'text','text':'answer'}]}
    client.post.side_effect=lambda *args,**kwargs:(payloads.append(kwargs['json']) or response)
    context=Mock();context.__enter__=Mock(return_value=client);context.__exit__=Mock(return_value=False)
    monkeypatch.setattr(module.httpx,'Client',lambda **kwargs:context)
    monkeypatch.setattr(module.keyring,'get_password',lambda *_:'fixture')
    candidate={'id':'primary','provider':provider,'model':'gpt-6.1-sol' if provider=='openai' else 'claude-opus-4-7',
        'reasoning_effort':'high','pricing_confirmed':True,'api_budget_usd':10}
    assert module.request_chat(candidate,[{'role':'system','content':'s'},{'role':'user','content':'u'}],'s',128,100)=='answer'
    assert payloads[0][expected_key]==('high' if provider=='openai' else {'effort':'high'})


def test_claude_chat_cli_receives_exact_model_and_effort(monkeypatch):
    import bughunter.model_routing as module
    captured=[]
    monkeypatch.setattr(module.shutil,'which',lambda _: '/fixture/claude')
    monkeypatch.setattr('bughunter.providers.claude_status',lambda:{'subscription_ready':True})
    monkeypatch.setattr(module.subprocess,'run',lambda args,**kwargs:(captured.append(args) or SimpleNamespace(returncode=0,stdout='answer')))
    module.request_chat({'provider':'claude_code','model':'claude-sonnet-4-6','reasoning_effort':'medium'},[{'role':'user','content':'u'}],'s',128,100)
    assert captured[0][captured[0].index('--model')+1]=='claude-sonnet-4-6'
    assert captured[0][captured[0].index('--effort')+1]=='medium'


@pytest.mark.parametrize('refresh', [False, True])
def test_chat_catalog_loads_full_connected_api_list_and_refreshes_on_request(tmp_path,monkeypatch,refresh):
    app=Application(tmp_path,primary=False)
    calls=[]
    try:
        monkeypatch.setattr(app.media,'status',lambda:{})
        monkeypatch.setattr(app.settings,'credentials',lambda:{'gemini':True})
        monkeypatch.setattr('bughunter.app.claude_status',lambda:{'subscription_ready':True})
        def discover(provider,**kwargs):
            calls.append((provider,kwargs))
            models=[{'id':f'gemini-catalog-{i}'} for i in range(1,42)] if provider=='gemini' else []
            return {'ok':True,'models':models,'detail':'Fixture model catalog'}
        monkeypatch.setattr(app.provider_models,'discover',discover)
        result=app.dispatch('models.available',{'refresh':refresh})
        gemini=next(row for row in result['providers'] if row['provider']=='gemini')
        assert len(gemini['models'])==41 and len(gemini['model_options'])==41
        assert gemini['models'][-1]=='gemini-catalog-41'
        assert ('gemini',{'api_base_url':'','refresh':refresh}) in calls
        assert not any(provider=='openai' for provider,_ in calls)
        if refresh:assert ('codex',{'refresh':True}) in calls
    finally:app.shutdown()


def test_claude_recommendations_merge_all_documented_versions_including_fable_5_2(tmp_path,monkeypatch):
    import bughunter.provider_models as module
    monkeypatch.setattr(module,'claude_status',lambda:{'subscription_ready':True})
    monkeypatch.setattr(module.shutil,'which',lambda _: '/fixture/claude')
    # The CLI recommendation list can still point its Fable row to 5.1.
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps({'type':'control_response','response':{'subtype':'success','response':{'models':[{'value':'claude-fable-5-1[1m]','resolvedModel':'claude-fable-5-1','supportsEffort':True,'supportedEffortLevels':['low','medium','high','xhigh','max']}]}}})))
    store=Store(tmp_path);catalog=ProviderModels(store).discover('claude_code')
    ids=[row['id'] for row in catalog['models']];assert len(ids)==len(set(ids))
    for model in ['claude-fable-5-2','claude-fable-5-1','claude-fable-5','claude-opus-4-8','claude-sonnet-5']:
        assert model in ids
    fable=next(row for row in catalog['models'] if row['id']=='claude-fable-5-2')
    assert fable['effort']['levels']==['low','medium','high','xhigh','max']
    assert fable['effort']['default']=='medium' and fable['effort']['source']=='documented'
    assert 'depends on your plan' in fable['detail']
    assert validate('claude_code','claude-fable-5-2','xhigh',store)=='xhigh'


def test_claude_explicit_provider_effort_caps_override_the_version_catalog():
    assert info('claude_code','claude-fable-5-2',{'levels':['low','medium'],'source':'provider_catalog'})['levels']==['low','medium']
    unsupported=info('claude_code','claude-fable-5-2',{'levels':[],'source':'provider_catalog'})
    assert unsupported['levels']==[] and unsupported['default'] is None
