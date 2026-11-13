"""Provider cooldowns preserve evidence and never resurrect cancelled sources."""
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.local_media import LocalMediaRuntime
from bughunter.media_retry import retry_plan, retry_due, subscription_retry
from bughunter.store import Store

CONFIG={'vision_provider':'codex_subscription','codex_vision_model':'gpt-6.1-sol'}
LIMIT='You have hit your usage limit. Try again at 4:23 AM.'


def test_only_exhausted_structured_windows_determine_reset():
    limits={'rateLimits':{'primary':{'usedPercent':100,'resetsAt':2000},
                          'secondary':{'usedPercent':21,'resetsAt':9000}},
            'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':100,'resetsAt':2000}}}}
    plan=retry_plan(LIMIT,CONFIG,limits=limits,current_time=1000)
    assert plan['retry_at']==2015 and plan['schedule_source']=='provider_reset'
    assert plan['provider']=='codex_subscription' and plan['model']=='gpt-6.1-sol'
    limits['rateLimits']['secondary']['usedPercent']=100
    assert retry_plan(LIMIT,CONFIG,limits=limits,current_time=1000)['retry_at']==9015


def test_ambiguous_cli_clock_and_available_metadata_use_bounded_backoff():
    limits={'rateLimits':{'primary':{'usedPercent':31,'resetsAt':20000}}}
    assert [retry_plan(LIMIT,CONFIG,n,limits=limits,current_time=1000)['retry_at']
            for n in (0,1,2,100)]==[1900,2800,4600,4600]
    for provider in ('local',):
        assert retry_plan(LIMIT,{**CONFIG,'vision_provider':provider},current_time=1000) is None
    assert retry_plan('Unreadable output',CONFIG,current_time=1000) is None
    assert retry_plan('Sign in first',CONFIG,current_time=1000) is None


def test_claude_limit_wait_has_its_own_provider_and_model():
    config={'vision_provider':'claude_subscription','claude_vision_model':'claude-opus-4-7'}
    plan=subscription_retry('You have hit your limit.',config)
    assert plan['provider']=='claude_subscription' and plan['model']=='claude-opus-4-7'
    assert plan['reason'].startswith('Claude')


@pytest.mark.parametrize('bad',[None,{},True,float('nan'),float('inf'),'tomorrow'])
def test_malformed_deadline_does_not_start_a_provider_request(bad):
    assert not retry_due({'provider_wait':{'retry_at':bad}},current_time=1000)


@pytest.fixture
def service(tmp_path,monkeypatch):
    store=Store(tmp_path)
    service=KnowledgeService(store,start_worker=False,
        media_runtime=SimpleNamespace(config=lambda:dict(CONFIG)))
    monkeypatch.setattr('bughunter.media_retry.time.time',lambda:1000)
    monkeypatch.setattr('bughunter.media_retry.subscription_retry',
        lambda error,config,attempts,**kwargs:retry_plan(error,config,attempts,current_time=1000))
    source=store.put('source',{'title':'Course','status':'queued','original_retained':True,
        'media_progress':{'phase':'frames','analyzed':12,'total':80,'timestamp':330}})
    def extract(*args):
        raise ValueError(LIMIT)
    monkeypatch.setattr(service,'_extract',extract)
    yield service,source
    service.shutdown()


def test_rate_limit_recovery_reuses_indexed_evidence_and_finishes_after_restart(service,monkeypatch):
    service,source=service
    root=service.store.root/'artifacts'/source['id'];root.mkdir()
    service._publish_media(source,[{'text':'Cobalt screenshot evidence.',
        'modality':'vision','start':0,'locator':'Frame at 00:00:00','context_key':'saved'}],root)
    before=service.get_source(source['id'])['chunks']
    result=service.process_source(source['id'])
    assert result['status']=='queued' and result['stage']=='provider_wait'
    assert result['media_progress']['phase']=='provider_wait'
    assert result['media_progress']['analyzed']==12
    assert result['provider_wait']['retry_at']==1900
    assert service.search('Cobalt')
    assert service.process_source(source['id'])['status']=='queued'
    restarted=KnowledgeService(service.store,start_worker=False,media_runtime=service.media_runtime)
    try:
        restarted.resume_pending()
        assert restarted._queue.empty()
        monkeypatch.setattr('bughunter.media_retry.time.time',lambda:1901)
        restarted.resume_pending();restarted.resume_pending()
        assert restarted._queue.qsize()==1
        assert restarted._queue.get_nowait()==source['id']
        monkeypatch.setattr(restarted,'_extract',lambda *args:([
            {'text':'New amber transcript evidence.','modality':'transcript'}],[]))
        result=restarted.process_source(source['id'])
        assert result['status']=='ready' and result.get('provider_wait') is None
        assert result['last_provider_wait']['attempt']==1
        assert restarted.search('Cobalt') and restarted.search('amber')
        assert sum(chunk['id']==before[0]['id'] for chunk in restarted.get_source(source['id'])['chunks'])==1
    finally:restarted.shutdown()


def test_stop_all_cancels_scheduled_retry_and_resume_does_not_restart_it(service,monkeypatch):
    service,source=service
    service.process_source(source['id'])
    service.store.set_setting('emergency_stop',True)
    service.cancel_pending()
    service.store.set_setting('emergency_stop',False)
    monkeypatch.setattr('bughunter.media_retry.time.time',lambda:5000)
    service.resume_pending()
    assert service._queue.empty()
    assert service.store.get('source',source['id'])['status']=='cancelled'
    assert service._claim(source['id']) is None


def test_manual_retry_clears_deadline_after_account_change(service):
    service,source=service
    service.process_source(source['id'])
    result=service.retry_source(source['id'])
    assert result['provider_wait'] is None and result['provider_retry_attempts']==0
    assert result['stage']=='queued'
    assert result['media_progress']['phase']=='frames'
    assert service._claim(source['id'])['status']=='processing'


def test_resume_preserves_provider_wait_stage_until_its_deadline(service):
    service,source=service
    service.process_source(source['id'])
    service.control_source(source['id'],'pause')
    resumed=service.control_source(source['id'],'resume')
    assert resumed['status']=='queued' and resumed['stage']=='provider_wait'
    assert resumed['provider_wait']['retry_at']==1900
    assert service._claim(source['id']) is None


def test_pause_and_cancel_apply_to_queued_provider_retries(service,monkeypatch):
    service,source=service
    service.process_source(source['id'])
    runtime=LocalMediaRuntime(service.store)
    runtime.control(source['id'],'pause')
    monkeypatch.setattr('bughunter.media_retry.time.time',lambda:5000)
    service.resume_pending()
    assert service._queue.empty() and service._claim(source['id']) is None
    runtime.control(source['id'],'cancel')
    current=service.store.get('source',source['id'])
    assert current['status']=='cancelled' and current['provider_wait'] is None
    service.resume_pending()
    assert service._queue.empty()


def test_cancellation_cannot_be_overwritten_by_late_provider_deferral(service):
    service,source=service
    service.store.put('source',{**source,'status':'cancelled','media_control':'cancel'})
    result=service._defer_provider(source['id'],retry_plan(LIMIT,CONFIG,current_time=1000))
    assert result['status']=='cancelled' and not result.get('provider_wait')


def test_retry_reads_only_usage_metadata_and_preserves_config(monkeypatch):
    requests=[]
    class Session:
        def __init__(self,**kwargs):
            assert kwargs['timeout']==20
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def call(self,method,params):
            requests.append((method,params))
            raise ValueError('Metadata unavailable')
    monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
    config=dict(CONFIG)
    assert subscription_retry('Unrelated error',config) is None
    assert not requests
    result=subscription_retry(LIMIT,config)
    assert requests==[('account/rateLimits/read',{})]
    assert result['schedule_source']=='backoff' and config==CONFIG


def test_cancel_during_usage_metadata_lookup_is_not_scheduled(monkeypatch):
    class Session:
        def __init__(self,**kwargs):pass
        def __enter__(self):raise InterruptedError('Stopped')
        def __exit__(self,*args):pass
    monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
    with pytest.raises(InterruptedError):subscription_retry(LIMIT,CONFIG)


def test_account_switch_releases_only_the_previous_accounts_wait(monkeypatch):
    from bughunter import media_retry
    limits={'accountId':'previous-account'}
    wait=retry_plan(LIMIT,CONFIG,limits=limits,current_time=1000)
    assert wait['account_fingerprint']!='previous-account'
    source={'provider_wait':wait}
    snapshot={'fingerprint':wait['account_fingerprint'],'allowed':True,'checked_at':1001}
    monkeypatch.setattr(media_retry._account_probe,'read',lambda check=None:snapshot)
    assert not retry_due(source,current_time=1001)
    snapshot['fingerprint']=media_retry._account_fingerprint({'accountId':'new-account'})
    snapshot['allowed']=False
    assert not retry_due(source,current_time=1001)
    snapshot['allowed']=True;snapshot['checked_at']=999
    assert not retry_due(source,current_time=1001)
    snapshot['checked_at']=1001
    assert retry_due(source,current_time=1001)


def test_account_probe_is_nonblocking_shared_and_metadata_only(monkeypatch):
    import threading,time
    from bughunter import media_retry
    entered=threading.Event();release=threading.Event();calls=[]
    class Session:
        def __init__(self,**kwargs):assert kwargs['timeout']==10
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def call(self,method,params):
            calls.append((method,params));entered.set();release.wait(2)
            return {'accountId':'new-account','ordinaryUsageAllowed':True}
    monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
    probe=media_retry._AccountProbe()
    try:
        assert probe.read() is None and entered.wait(1)
        for _ in range(10):assert probe.read() is None
        assert len(calls)==1
        release.set()
        for _ in range(100):
            if not probe.running:break
            time.sleep(.01)
        snapshot=probe.read()
        assert snapshot['allowed'] and snapshot['fingerprint']==media_retry._account_fingerprint({'accountId':'new-account'})
        assert calls==[('account/rateLimits/read',{})]
        assert 'accountId' not in snapshot
    finally:release.set()
