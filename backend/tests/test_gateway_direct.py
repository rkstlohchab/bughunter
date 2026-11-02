"""Built-in proxy verification requires two genuine, matching local receipts."""
from types import SimpleNamespace
import pytest

from bughunter.adapters import ToolService
from bughunter.store import Store


@pytest.fixture
def runner(tmp_path):
    store=Store(tmp_path)
    service=ToolService(store, None, None)
    return service,store


def test_no_gateway_runtime_no_verify(runner):
    service,store=runner
    with pytest.raises(ValueError,match='Start the built-in gateway'):
        service.gateway_verify_direct()
    assert not store.setting('gateway',{}).get('verified_at')


def test_missing_certificate_no_verify(runner):
    service,store=runner
    store.set_setting('gateway_runtime',{'port':8091})
    with pytest.raises(ValueError,match='CA not ready'):
        service.gateway_verify_direct()


def test_both_receipts_required(runner,monkeypatch):
    service,store=runner
    store.set_setting('gateway_runtime',{'port':8091})
    ca=store.root/'gateway'/'mitmproxy-ca-cert.pem'; ca.parent.mkdir(exist_ok=True);ca.write_text('fake-test-CA')
    import ssl,httpx
    monkeypatch.setattr(ssl,'create_default_context',lambda **kwargs:object())
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['proxy']=='http://127.0.0.1:8091'
            assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def get(self,url,headers):
            scheme=url.split(':')[0]
            token=headers['X-BugHunter-Probe']
            store.set_setting('gateway_last_probe',{'nonce':token,'scheme':scheme,'at':1})
            return SimpleNamespace(status_code=200,json=lambda:{'nonce':token})
    monkeypatch.setattr(httpx,'Client',Client)
    result=service.gateway_verify_direct()
    assert result['verified'] is True
    assert store.setting('gateway')['mode']=='built_in'
    assert store.setting('gateway')['verified_protocols']==['http','https']


def test_wrong_https_receipt_fails_closed(runner,monkeypatch):
    service,store=runner
    store.set_setting('gateway_runtime',{'port':8091})
    ca=store.root/'gateway'/'mitmproxy-ca-cert.pem';ca.parent.mkdir(exist_ok=True);ca.write_text('fake-test-CA')
    import ssl,httpx
    monkeypatch.setattr(ssl,'create_default_context',lambda **kwargs:object())
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def get(self,url,headers):
            scheme=url.split(':')[0]
            token=headers['X-BugHunter-Probe']
            store.set_setting('gateway_last_probe',{'nonce':token,'scheme':'http','at':1})
            return SimpleNamespace(status_code=200,json=lambda:{'nonce':token})
    monkeypatch.setattr(httpx,'Client',Client)
    with pytest.raises(ValueError,match='verification failed'):
        service.gateway_verify_direct()
    assert not store.setting('gateway',{}).get('verified_at')
