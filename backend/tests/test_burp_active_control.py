"""Only a real, scoped history seed may start an explicitly approved audit."""
from types import SimpleNamespace
import pytest
from bughunter.burp_history import BurpHistory, safe_audit_status
from bughunter.autopilot import choose_workflow
from bughunter.desktop_planner import visible_tabs, validate_plan
from bughunter.store import Store

PROJECT={'id':'p', 'authorization':'Written authorization for this lab',
         'scope':['https://lab.example/'], 'exclusions':['https://lab.example/forbidden'],
         'read_only_paths':['/notes']}


class FakeResponse:
    def __init__(self,payload): self.payload=payload
    def raise_for_status(self):return None
    def json(self):return self.payload


class Bridge:
    def __init__(self):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url,headers):
        self.calls.append(('GET',url))
        if url.endswith('/history'):
            return FakeResponse({'items':[
                {'method':'GET','scheme':'https','host':'lab.example','path':'/notes',
                 'port':-1,'status':200,'burp_history_id':42},
                {'method':'GET','scheme':'https','host':'lab.example','path':'/forbidden',
                 'port':-1,'status':200,'burp_history_id':90},
                {'method':'POST','scheme':'https','host':'lab.example','path':'/notes',
                 'port':-1,'status':200,'burp_history_id':43}]})
        if url.endswith('/audit/status'):
            return FakeResponse({'tracked':True,'history_id':42,'requests':5,
                                 'errors':0,'reported_issues':1,'status':'Testing'})
        raise AssertionError(url)
    def post(self,url,headers,content=None):
        self.calls.append(('POST',url,content))
        if url.endswith('/audit/start'):
            return FakeResponse({'started':True,'history_id':int(content)})
        if url.endswith('/audit/stop'):
            return FakeResponse({'stopped':True})
        raise AssertionError(url)


@pytest.fixture()
def prepared(monkeypatch,tmp_path):
    import bughunter.burp_history as mod
    store=Store(tmp_path)
    store.put('project',PROJECT,id='p')
    fake=Bridge()
    monkeypatch.setattr(mod,'_token',lambda:'f'*32)
    monkeypatch.setattr(mod.httpx,'Client',lambda **kw:fake)
    app=SimpleNamespace(store=store,policy=SimpleNamespace(project=lambda id:PROJECT if id=='p' else {}))
    return BurpHistory(app),fake


def test_start_needs_explicit_approval_without_http(prepared):
    bridge,fake=prepared
    with pytest.raises(ValueError,match='Explicit authorization'):
        bridge.dispatch('burp.audit.start',{'project_id':'p','history_id':42})
    assert fake.calls==[]


def test_start_uses_fresh_scoped_history_and_safe_method(prepared):
    bridge,fake=prepared
    for number in (90,999,43):
        with pytest.raises(ValueError):
            bridge.dispatch('burp.audit.start',{'project_id':'p','history_id':number,'approved':True})
    assert not any(x[0]=='POST' for x in fake.calls)
    result=bridge.dispatch('burp.audit.start',{'project_id':'p','history_id':42,'approved':True})
    assert result['started'] and result['history_id']==42
    assert fake.calls[-1]==('POST','http://127.0.0.1:18765/audit/start','42')


def test_start_rejects_non_approved_readonly_path(prepared):
    bridge,fake=prepared
    bridge.app.policy.project=lambda id:{**PROJECT,'read_only_paths':[]}
    with pytest.raises(ValueError,match='read-only path'):
        bridge.dispatch('burp.audit.start',{'project_id':'p','history_id':42,'approved':True})
    assert not any(x[0]=='POST' for x in fake.calls)


def test_status_and_stop_require_real_ack(prepared):
    bridge,fake=prepared
    with pytest.raises(ValueError,match='Confirm'):
        bridge.dispatch('burp.audit.stop',{})
    assert fake.calls==[]
    report=bridge.dispatch('burp.audit.status',{})
    assert report['tracked'] and report['requests']==5 and report['reported_issues']==1
    assert bridge.dispatch('burp.audit.stop',{'approved':True})['stopped']


def test_untrusted_status_sanitized():
    with pytest.raises(ValueError):safe_audit_status({'tracked':'yes'})
    clean=safe_audit_status({'tracked':True,'history_id':42,'requests':3,'cookie':'secret',
                             'status':'A'*1000})
    assert 'cookie' not in clean and len(clean['status'])==180


def test_chat_routes_and_tab_limited_to_real_observation():
    assert choose_workflow('Run Burp scan #42')=='burp_scan_start'
    assert choose_workflow('Burp scan status')=='burp_scan_status'
    assert choose_workflow('Stop Burp scan')=='burp_scan_stop'
    assert choose_workflow('Read Burp Scanner findings')=='burp_issues'
    obs='Window: Burp Suite\nTab: Proxy\nTab: Scanner\nText: Tab: Uninstall'
    assert visible_tabs(obs)=={'proxy','scanner'}
    assert validate_plan('{"action":"click_tab","value":"Scanner","reason":"navigate"}',obs,True)['action']=='click_tab'
    with pytest.raises(ValueError,match='not observed'):
        validate_plan('{"action":"click_tab","value":"Uninstall","reason":"navigate"}',obs,True)
    with pytest.raises(ValueError,match='read-only'):
        validate_plan('{"action":"click_tab","value":"Scanner","reason":"navigate"}',obs,False)


def test_mac_agent_only_focuses_observed_nonsecret_fields():
    from bughunter.desktop_planner import visible_fields
    observed='Window: Finder\nText field label: Search\nText field label: Password\nText: API Key'
    assert visible_fields(observed)=={'search','password'}
    assert validate_plan('{"action":"focus_field","value":"Search","reason":"Find file"}',observed,True)['action']=='focus_field'
    with pytest.raises(ValueError,match='Protected'):
        validate_plan('{"action":"focus_field","value":"Password","reason":"Login"}',observed,True)
    with pytest.raises(ValueError,match='not observed'):
        validate_plan('{"action":"focus_field","value":"API Key","reason":"Login"}',observed,True)


def test_stop_remains_available_after_global_latch(prepared):
    bridge,fake=prepared
    bridge.app.store.set_setting('emergency_stop',True)
    bridge.app.store.set_setting('burp_audit_active',True)
    with pytest.raises(ValueError,match='paused'):
        bridge.dispatch('burp.audit.start',{'project_id':'p','history_id':42,'approved':True})
    assert bridge.dispatch('burp.audit.stop',{'approved':True})['stopped']
    assert bridge.app.store.setting('burp_audit_active') is False
    assert fake.calls==[('POST','http://127.0.0.1:18765/audit/stop',None)]
