"""Real read-only composition, not an asserted autonomous vulnerability."""
from types import SimpleNamespace
import pytest
from bughunter.autopilot import Autopilot, choose_workflow
from bughunter.store import Store


class FakeBurp:
    def __init__(self, connected=True):
        self.calls=[]
        self.connected=connected

    def dispatch(self, method, params):
        self.calls.append((method,params))
        if method=='burp.health': return {'connected':self.connected,'service':'Montoya'}
        if method=='burp.history':return {'items':[{'burp_history_id':17,'url':'https://lab.example/','method':'GET','status':200,'source':'Burp HTTP history (metadata and header-presence checks only)'}], 'passive_review_leads':[{'history_id':17,'observation':'HSTS missing', 'verification_needed':'Review requirements'}]}
        if method=='burp.issues':return {'issues':[{'url':'https://lab.example/','name':'Example finding', 'severity':'LOW', 'confidence':'TENTATIVE','source':'Burp Scanner notification'}]}
        raise AssertionError('Unknown or modifying Burp method: '+method)


def factory(tmp_path, connected=True):
    store=Store(tmp_path/'db')
    store.put('project',{'name':'Local lab','scope':['https://lab.example/'],'authorization':'owned', 'read_only_paths':['/']},id='lab')
    bridge=FakeBurp(connected)
    return Autopilot(SimpleNamespace(store=store,burp_history=bridge)),bridge,store


def test_chat_routes_composite_review_and_persists_reproducible_receipts(tmp_path):
    auto,bridge,store=factory(tmp_path)
    request={'message':'Use Burp Suite to review the website security findings','project_id':'lab'}
    assert choose_workflow(request['message'])=='burp_review'
    plan=auto.plan(**request)
    assert plan['ready'] and not plan['requires_approval']
    output=auto.start(**request)
    assert output['status']=='completed'
    result=output['result']
    assert len(result['history'])==1 and len(result['issues'])==1
    assert [row[0] for row in bridge.calls]==['burp.health','burp.history','burp.issues']
    assert [item['step'] for item in result['receipts']]==['bridge_health','proxy_history','scanner_notifications']
    assert 'no new network scan' in result['assessment'].lower()
    evidence=auto.export_evidence('lab')
    assert evidence['count']==2
    assert 'observation_hash' in evidence['content']


def test_missing_project_does_not_contact_burp(tmp_path):
    auto,bridge,_=factory(tmp_path)
    with pytest.raises(ValueError,match='Missing setup'):
        auto.start(message='review Burp website findings')
    assert bridge.calls==[]


def test_bridge_health_failure_records_failed_task_without_fake_results(tmp_path):
    auto,bridge,store=factory(tmp_path,connected=False)
    with pytest.raises(ValueError,match='authenticated connection'):
        auto.start(message='Use Burp to review the website findings',project_id='lab')
    tasks=store.list('work_task')
    assert tasks and tasks[0]['status']=='failed'
    assert [x[0] for x in bridge.calls]==['burp.health']


def test_active_scan_commands_still_have_priority():
    assert choose_workflow('Run Burp scan #42 on this website')=='burp_scan_start'
    assert choose_workflow('Stop Burp scan for this website')=='burp_scan_stop'
    assert choose_workflow('Show Burp Scanner findings')=='burp_issues'
