import threading
import time
import pytest
from bughunter.inference_gate import local_inference, GatedClient

def test_shared_gate_wait_is_cancellable():
    stop=threading.Event(); entered=threading.Event(); outcome=[]
    def check():
        if stop.is_set(): raise InterruptedError()
    def worker():
        try:
            entered.set()
            with local_inference(check): outcome.append('entered')
        except InterruptedError: outcome.append('cancelled')
    with local_inference():
        thread=threading.Thread(target=worker); thread.start(); entered.wait(1); stop.set(); thread.join(1)
    assert outcome==['cancelled']
    with local_inference(): pass

def test_gate_released_on_failure():
    with pytest.raises(ValueError):
        with local_inference(): raise ValueError()
    with local_inference(): pass

def test_inflight_request_is_cancelled_and_gate_released():
    stop=threading.Event(); started=threading.Event(); closed=threading.Event(); outcome=[]
    class Client:
        def post(self,url,**kwargs):
            started.set()
            assert closed.wait(2)
            raise InterruptedError()
        def close(self): closed.set()
    def check():
        if stop.is_set(): raise InterruptedError()
    def work():
        try: GatedClient(Client(),check).post('http://localhost/api/chat',json={})
        except InterruptedError: outcome.append('cancelled')
    thread=threading.Thread(target=work);thread.start();assert started.wait(2)
    stop.set();thread.join(2)
    assert not thread.is_alive()
    assert outcome==['cancelled'] and closed.wait(1)
    with local_inference(): pass


def test_supplied_transport_session_and_headers_are_preserved():
    import httpx
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'message':{'content':'ok'}},headers={'set-cookie':'session=updated'})
    with httpx.Client(transport=httpx.MockTransport(handler),headers={'X-Session':'preserved'},
                      cookies={'initial':'cookie'},params={'shared':'value'}) as client:
        response=GatedClient(client).post('http://localhost/api/chat',json={'model':'selected'})
        assert response.json()['message']['content']=='ok'
        assert client.cookies.get('session')=='updated'
    assert len(calls)==1
    assert calls[0].headers['X-Session']=='preserved'
    assert 'initial=cookie' in calls[0].headers['cookie']
    assert calls[0].url.params['shared']=='value'

def test_cancelled_uninterruptible_client_keeps_gate_until_request_finishes():
    stop=threading.Event();first_started=threading.Event();closed=threading.Event()
    release=threading.Event();second_started=threading.Event();cancelled=threading.Event()
    class First:
        def post(self,*a,**kw):
            first_started.set();assert release.wait(3);return 'first'
        def close(self):closed.set()
    class Second:
        def post(self,*a,**kw):second_started.set();return 'second'
    def check():
        if stop.is_set():raise InterruptedError()
    def first():
        try:GatedClient(First(),check).post('http://localhost/api/chat')
        except InterruptedError:cancelled.set()
    one=threading.Thread(target=first);one.start();assert first_started.wait(1)
    stop.set();one.join(1)
    assert cancelled.is_set() and closed.wait(1)
    two=threading.Thread(target=lambda:GatedClient(Second()).post('http://localhost/api/chat'))
    two.start()
    assert not second_started.wait(.2)
    release.set();two.join(2)
    assert second_started.is_set() and not two.is_alive()
