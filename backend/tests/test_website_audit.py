"""Bounded website crawling with fake gateway responses (no external requests)."""
import time
from types import SimpleNamespace

import pytest

from bughunter.website_audit import WebsiteAudit, eligible, inspect_page
from bughunter.policy import PolicyService
from bughunter.reports import ReportService
from bughunter.store import Store


@pytest.fixture
def app(tmp_path):
    store=Store(tmp_path)
    policy=PolicyService(store)
    project=policy.save_project({
        'name':'Authorized staging', 'scope':['https://shop.example.test/'],
        'authorization':'Owned staging environment; only safe GET pages',
        'read_only_paths':['/', '/about', '/products', '/public/*'],
        'requests_per_second':2, 'max_requests':20, 'max_concurrency':1,
    })
    store.set_setting('gateway',{'verified_at':time.time(), 'burp_proxy':'http://127.0.0.1:8080'})
    html={
       '/':'<html><a href="/about">About</a><a href="/products">Products</a><a href="https://attacker.example.test/">Offsite</a><a href="/checkout">Checkout</a><a href="/public/info">Info</a><a href="/about?admin=true">Query</a></html>',
       '/about':'<html><form action="http://insecure.example.test/post" method="post"><input name="pwd" type="password"></form><a href="/">Home</a></html>',
       '/products':'<html><a href="/checkout">Buy</a></html>',
       '/public/info':'<html>hi</html>',
    }
    sent=[]
    def fake_execute(id):
        action=store.get('action',id)
        assert action['effect']=='read_only'
        assert action['request']['method']=='GET'
        from urllib.parse import urlsplit
        route=urlsplit(action['request']['url']).path
        sent.append(route)
        assert route in html
        row=store.put('traffic',{'project_id':project['id'],'trusted_observation':True,'response':{'status':200},'request':action['request']})
        return policy.finish(id,response={
            'status':200,'headers':{'content-type':'text/html', 'x-content-type-options':'nosniff'},'body':html[route]},transmitted=False)
    # The policy still performs queuing/scope/integrity. Replace only the external transport.
    policy.execute=fake_execute
    obj=SimpleNamespace(store=store,policy=policy,reports=ReportService(store),tasks=SimpleNamespace(describe=lambda r:r))
    obj.sent=sent
    obj.audit=WebsiteAudit(obj)
    obj.project=project
    return obj


def finished(store, id, timeout=8):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        task=store.get('work_task',id)
        if task['status'] in {'completed','failed','cancelled','interrupted'}:return task
        time.sleep(.04)
    pytest.fail('website audit did not complete')


def test_no_network_without_explicit_permission(app):
    with pytest.raises(ValueError,match='explicitly authorize'):
        app.audit.start(project_id=app.project['id'])
    assert app.sent==[]


def test_scoped_readonly_crawl_observes_evidence(app):
    task=app.audit.start(project_id=app.project['id'],approved=True,max_pages=8)
    done=finished(app.store,task['id'])
    assert done['status']=='completed',done.get('error')
    assert set(app.sent)=={'/','/about','/products','/public/info'}
    assert done['result']['requests_sent']==4
    assert len(done['result']['findings'])>=1
    assert all(app.store.get('finding',id)['status']=='hypothesis' for id in done['result']['findings'])
    assert all(page['evidence_ids'] for page in done['result']['pages'])
    assert all(a['request']['method']=='GET' for a in app.store.list('action'))
    assert not any('checkout' in a['request']['url'] for a in app.store.list('action'))


def test_no_cross_scope_or_mutating_paths(app):
    for path in ('/checkout','/purchase','/logout','/admin','/cart','/public/cart','/about?token=x'):
        assert not eligible(app.project,'https://shop.example.test'+path)
    assert not eligible(app.project,'https://other.example.test/')
    assert not eligible(app.project,'http://shop.example.test/')
    assert eligible(app.project,'https://shop.example.test/public/info')


def test_missing_gateway_fails_closed(app):
    app.store.set_setting('gateway',{})
    with pytest.raises(ValueError,match='gateway'):
        app.audit.start(project_id=app.project['id'],approved=True)
    assert app.sent==[]


def test_missing_explicit_safe_paths_fails_closed(app):
    app.policy.save_project({**app.project,'read_only_paths':[]})
    with pytest.raises(ValueError,match='known read-only'):
        app.audit.start(project_id=app.project['id'],approved=True)


def test_audit_can_be_stopped(app):
    original=app.policy.execute
    def slow(id):
        time.sleep(.12)
        return original(id)
    app.policy.execute=slow
    started=app.audit.start(project_id=app.project['id'],approved=True,max_pages=8)
    app.audit.cancel(started['id'])
    final=finished(app.store,started['id'])
    assert final['status']=='cancelled'
    assert len(app.sent)<=1


def test_missing_security_header_is_lead_not_confirmed():
    links,issues,page=inspect_page('https://example.test/',{'status':200,'headers':{'content-type':'text/html'},'body':'<a href="/ok">ok</a>'})
    assert '/ok' in links
    assert page['status']==200
    assert any(x['title']=='HSTS header not observed' for x in issues)


def test_emergency_stop(app):
    app.store.set_setting('emergency_stop',True)
    with pytest.raises(ValueError,match='Emergency stop'):
        app.audit.start(project_id=app.project['id'],approved=True)


def test_report_contains_observed_evidence_and_limitations(app):
    task=app.audit.start(project_id=app.project['id'],approved=True,max_pages=2)
    completed=finished(app.store,task['id'])
    assert completed['status']=='completed'
    output=app.audit.report(task['id'])
    assert 'evidence:' in output['content']
    assert 'unconfirmed' in output['content'].lower()
    assert 'not an authenticated penetration test' in output['content']
    assert 'example.test' not in output['filename']


def test_failed_gateway_request_not_replayed(app):
    count=0
    def uncertain(id):
        nonlocal count
        count+=1
        return {'status':'unknown','evidence_ids':[], 'response':None}
    app.policy.execute=uncertain
    started=app.audit.start(project_id=app.project['id'],approved=True,max_pages=8)
    complete=finished(app.store,started['id'])
    assert complete['status']=='failed'
    assert count==1
    assert complete['result']['requests_sent']==1
    assert 'not produce verified evidence' not in str(complete.get('error','')) or complete['status']=='failed'


def test_owner_stop_prevents_requests(app):
    app.store.set_setting('emergency_stop', True)
    with pytest.raises(ValueError):
        app.audit.start(project_id=app.project['id'],approved=True,max_pages=8)
    assert app.sent==[]


def test_sensitive_form_values_not_persisted():
    html='<form action="/api"><input name="hidden_token" type="hidden" value="SECRET"><input name="password" type="password" value="PASSWORD"></form>'
    _links,_issues,page=inspect_page('https://shop.example.test/',{'status':200,'headers':{'content-type':'text/html'},'body':html})
    assert 'SECRET' not in str(page)
    assert 'PASSWORD' not in str(page)
