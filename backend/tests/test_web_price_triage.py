"""Verification for read-only price-integrity and web security review."""
from types import SimpleNamespace
import pytest
from bughunter.web_pentest import review_traffic
from bughunter.burp_history import safe_item, BurpHistory
from bughunter.autopilot import Autopilot, choose_workflow
from bughunter.store import Store

PROJECT={'id':'shop','name':'Owned staging','authorization':'owned','scope':['https://shop.test/'],'read_only_paths':['/']}

def make(method='POST', path='/checkout/quote', fields=None, host='shop.test', rid=9):
    return {'method':method,'scheme':'https','host':host,'path':path,'port':-1,
            'status':200,'burp_history_id':rid,'pricing_fields':fields or [],
            'response_checks':{'hsts':True,'csp':True,'nosniff':True}}

def test_allowlisted_names_only_and_scope_filtering():
    record=safe_item(make(fields=['price','quantity','customer_password','api_key','price','coupon']),PROJECT)
    assert record['pricing_fields']==['coupon','price','quantity']
    assert 'api_key' not in repr(record)
    assert safe_item(make(host='other.test',fields=['price']),PROJECT) is None
    assert safe_item(make(path='/checkout?token=secret',fields=['price']),PROJECT) is None

def test_passive_price_lead_is_not_reported_as_proven_exploit():
    record=safe_item(make(fields=['price','quantity','currency']),PROJECT)
    report=review_traffic([record,record],[],[])
    assert report['counts']['observed_requests']==2
    assert report['counts']['price_review_leads']==1
    lead=report['price_and_checkout_review'][0]
    assert lead['state']=='review_needed'
    assert lead['severity']=='not_assigned'
    assert 'NOT established' in lead['proof']
    assert report['not_tested']
    assert 'No checkout' in report['not_tested'][0]

def test_checkout_path_without_fields_is_review_only():
    record=safe_item(make(method='GET',path='/orders', fields=[]),PROJECT)
    report=review_traffic([record],[],[])
    assert report['price_and_checkout_review'][0]['category']=='checkout_business_logic'
    assert report['price_and_checkout_review'][0]['evidence']['pricing_fields']==[]

def test_unrelated_paths_not_flagged_and_scanner_facts_separate():
    record=safe_item(make(method='GET',path='/public/catalog', fields=[]),PROJECT)
    alert={'source':'Burp Scanner notification','url':'https://shop.test/catalog','severity':'LOW',
           'name':'Missing header','confidence':'TENTATIVE'}
    result=review_traffic([record],[alert],[{'history_id':9,'observation':'CSP not set'}])
    assert result['price_and_checkout_review']==[]
    assert result['scanner_notifications'][0]['state']=='scanner_report_unverified'
    assert result['header_observations'][0]['state']=='passive_observation_unverified'

def test_reject_raw_unbounded_pricing_fields():
    for fields in ('secret','price', ['price']*33, [12,'price']):
        rec=make(fields=fields)
        assert safe_item(rec,PROJECT)['pricing_fields']==[]

def test_chat_routing_keeps_burp_scan_controls_first():
    assert choose_workflow('Find price manipulation flaws with Burp on my website')=='web_pentest'
    assert choose_workflow('Perform a website penetration test with Burp')=='web_pentest'
    assert choose_workflow('Run Burp scan #40 on this website')=='burp_scan_start'
    assert choose_workflow('Show Burp history')=='burp_history'

class FakeBurp:
    def __init__(self): self.calls=[]
    def dispatch(self,method,params):
        self.calls.append(method)
        if method=='burp.web.review':
            item=safe_item(make(fields=['total','coupon']),PROJECT)
            return {'project_id':'shop','bridge_health':{'connected':True},
                    'history':[item],'issues':[],
                    'review':review_traffic([item],[],[]),
                    'read_only':True,'source':'Authenticated local Burp'}
        raise AssertionError('Unexpected tool action: '+method)

def test_autopilot_readonly_report_and_evidence_export(tmp_path):
    store=Store(tmp_path/'db')
    store.put('project',PROJECT,id='shop')
    burp=FakeBurp()
    auto=Autopilot(SimpleNamespace(store=store,burp_history=burp))
    request={'message':'Find price manipulation flaws with Burp on my website','project_id':'shop'}
    assert auto.plan(**request)['ready']
    assert not auto.plan(**request)['requires_approval']
    run=auto.start(**request)
    assert run['status']=='completed'
    assert burp.calls==['burp.web.review']
    assert run['result']['read_only']
    data=auto.export_evidence('shop')['content']
    assert 'checkout_price_review_lead' in data
    assert 'not_verified' in data
    assert 'total' in data and 'coupon' in data

def test_unauthorized_project_cannot_use_bridge(tmp_path):
    store=Store(tmp_path/'db')
    store.put('project',{**PROJECT,'authorization':''},id='shop')
    burp=FakeBurp()
    auto=Autopilot(SimpleNamespace(store=store,burp_history=burp))
    with pytest.raises(ValueError,match='Missing setup'):
        auto.start(message='Find price manipulation flaws on my website',project_id='shop')
    assert not burp.calls


def test_local_price_lab_requires_approval_not_burp(tmp_path):
    store=Store(tmp_path/'db')
    calls=[]
    class LocalCartLab:
        def start(self,approved=False):
            calls.append(approved)
            return {'id':'local_lab_job','kind':'cart_lab','status':'starting'}
    auto=Autopilot(SimpleNamespace(store=store,commerce=LocalCartLab()))
    text='Test price manipulation in my disposable local test shop'
    assert choose_workflow(text)=='commerce_lab'
    assert auto.plan(message=text)['requires_approval']
    with pytest.raises(ValueError,match='Confirm checkout'):
        auto.start(message=text)
    assert calls==[]
    run=auto.start(message=text,approved=True)
    assert run['kind']=='cart_lab' and calls==[True]
