"""P9 evidence-bound verification and source isolation."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from bughunter.store import Store
from bughunter.verification_p9 import VerificationP9, compare_entity_snapshots, assess_staging_task


def service(tmp_path):
    store = Store(tmp_path)
    project = store.put('project', {'name': 'Offline Project'})
    return VerificationP9(SimpleNamespace(store=store)), project['id']


def frame(x=1, health=100, pid=234):
    return {'pid': pid, 'entities': [{'id': 10, 'x': x, 'y': 5.0, 'health': health, 'team': 1},
                                    {'id': 20, 'x': 8.0, 'y': 7.0, 'health': 90}]}


def stage_task(store, project_id, status='completed', price=200):
    rows=[]
    for name in ('baseline','client_price_lowered','client_price_zero'):
        rows.append({'case': name, 'expected_total_minor': 200,
                     'observed_total_minor': price, 'evidence_ids': ['traffic'+name]})
    all_ids = ['manifest-receipt', 'quote-receipt'] + [row['evidence_ids'][0] for row in rows]
    for identifier in all_ids:
        route = '/.well-known/bughunter-price-audit.json' if identifier == 'manifest-receipt' else '/__bughunter__/quote'
        store.put('traffic', {'project_id': project_id, 'origin':'gateway', 'trusted_observation':True,
                              'request': {'method':'GET' if identifier=='manifest-receipt' else 'POST',
                                          'url': 'https://staging.invalid'+route},
                              'response':{'status':200, 'body':json.dumps({'total_minor':price,
                                'quote_only':True,'order_created':False,'payment_initiated':False})}}, id=identifier)
    return store.put('work_task',{'project_id': project_id, 'kind':'staging_price','status':status,
                                'result':{'cases':rows,'evidence_ids':['manifest-receipt','quote-receipt'],
                                          'transactional_actions':0}})


def test_real_entity_snapshot_comparison_and_private_digest(tmp_path):
    service_p9, project_id = service(tmp_path)
    one, two = frame(), frame(4, 95)
    record = service_p9.compare_game(project_id,one,two)
    report = record['report']
    assert report['result']=='observed_changes'
    assert report['deltas'][0]['delta_x']==3
    assert report['deltas'][0]['health_delta']==-5
    assert report['first']['sha256'] != report['second']['sha256']
    assert 'entities' not in report['first']
    assert report['verification_level']=='decoded_observation_only'
    assert service_p9.get(project_id,record['id'])['id']==record['id']
    assert service_p9.summary(project_id)['evidence_reviews']==1


def test_invalid_entity_data_and_identity_mismatch():
    with pytest.raises(ValueError, match='Duplicate'):
        compare_entity_snapshots({'pid':1, 'entities':[{'id':1,'x':1,'y':1}, {'id':1,'x':2,'y':2}]}, frame())
    for invalid in [float('nan'), True, float('inf'), 1_000_001]:
        broken=frame(); broken['entities'][0]['x']=invalid
        with pytest.raises(ValueError):compare_entity_snapshots(broken,frame())
    row=compare_entity_snapshots(frame(),frame(pid=987))
    assert row['first']['pid'] != row['second']['pid']
    assert 'cross-restart' in row['limitations']


def test_no_change_and_disappeared_entity():
    base=frame()
    unchanged=compare_entity_snapshots(base,frame())
    assert unchanged['result']=='no_changes_observed'
    different=frame();different['entities']=[{'id':1,'x':0,'y':0}]
    row=compare_entity_snapshots(base,different)
    assert row['common_count']==0 and row['new_count']==1 and row['missing_count']==2


def test_staging_receipts_success_and_detection(tmp_path):
    manager, project_id=service(tmp_path)
    ok=stage_task(manager.store,project_id)
    row=manager.review_staging(project_id,ok['id'])
    assert row['report']['assessment']=='staging_price_invariants_passed'
    assert row['report']['receipts_correlated'] is True
    bad=stage_task(manager.store,project_id,price=1)
    assert manager.review_staging(project_id,bad['id'])['report']['assessment']=='staging_price_integrity_review_needed'
    incomplete=stage_task(manager.store,project_id,status='failed')
    assert manager.review_staging(project_id,incomplete['id'])['report']['assessment']=='insufficient_evidence'


def test_missing_gateway_receipt_fails_early(tmp_path):
    manager, project_id=service(tmp_path)
    row=stage_task(manager.store,project_id)
    report=row['result'];report['cases'][0]['evidence_ids']=[]
    manager.store.put('work_task',{**row,'result':report},id=row['id'])
    with pytest.raises(ValueError,match='receipt'):manager.review_staging(project_id,row['id'])


def test_project_isolation_and_model_read_only(tmp_path):
    manager, project_id=service(tmp_path)
    other=manager.store.put('project',{'name':'Other'})['id']
    row=manager.compare_game(project_id,frame(),frame(10))
    assert manager.list(other)==[]
    with pytest.raises(ValueError):manager.get(other,row['id'])
    task=stage_task(manager.store,project_id)
    with pytest.raises(ValueError):manager.review_staging(other,task['id'])
    with pytest.raises(ValueError):manager.dispatch('verification.p9.execute',{'project_id':project_id})


def test_research_interruption_handoff_no_secret_content(tmp_path):
    manager, project_id=service(tmp_path)
    manager.store.put('research_browser_p3',{'project_id':project_id,'status':'waiting_for_human',
                      'blocker':'captcha','captured_ids':['capture'], 'password':'never return this'})
    blocked=manager.research_blockers(project_id)
    assert len(blocked)==1 and blocked[0]['blocker']=='captcha'
    assert 'password' not in json.dumps(blocked)
    assert blocked[0]['capture_count']==1


def test_loopback_radar_capture_is_real_observation(tmp_path):
    manager, project_id=service(tmp_path)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path=='/state'
            body=json.dumps(frame()).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*_):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
    try:
        result=manager.capture_local(project_id,server.server_port)
        assert result==frame()
    finally:
        server.server_close();thread.join(timeout=2)
    with pytest.raises(ValueError):manager.capture_local(project_id,80)
    with pytest.raises(ValueError):manager.capture_local('unknown',8787)


def test_untrusted_traffic_cannot_be_promoted_to_stage_proof(tmp_path):
    manager, project_id=service(tmp_path)
    row=stage_task(manager.store,project_id)
    gateway=manager.store.get('traffic','manifest-receipt')
    manager.store.put('traffic', {**gateway,'origin':'imported','trusted_observation':False},id=gateway['id'])
    audit=manager.review_staging(project_id,row['id'])['report']
    assert audit['assessment']=='insufficient_evidence'
    assert audit['receipts_correlated'] is False


def test_model_can_inspect_p9_but_not_start_verification(tmp_path):
    from bughunter.model_tools import ModelTools, TOOL_SPECS
    manager, project_id=service(tmp_path)
    app=SimpleNamespace(store=manager.store,verification_p9=manager)
    run=manager.store.put('run', {'project_id':project_id,'status':'running'})
    model=ModelTools(app,run['id'],study=True)
    assert 'get_advanced_verification' in {s['name'] for s in TOOL_SPECS}
    result=model.call('get_advanced_verification',{})
    assert result['phase']=='P9'
    with pytest.raises(ValueError,match='not available'):
        model.call('verification.p9.compare_entities',{'project_id':project_id})
