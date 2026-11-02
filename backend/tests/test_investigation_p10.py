"""P10: observed timelines, source integrity, project isolation, agent read-only boundary."""
from types import SimpleNamespace
import copy
import pytest

from bughunter.store import Store
from bughunter.investigation_p10 import InvestigationP10, analyze_entity_timeline
from bughunter.verification_p9 import VerificationP9


def instance(tmp_path):
    store=Store(tmp_path)
    one=store.put('project', {'name':'Game test'})['id']
    two=store.put('project', {'name':'Other'})['id']
    app=SimpleNamespace(store=store)
    return InvestigationP10(app), VerificationP9(app), one, two


def frame(x=0, pid=101, health=100, include=True, team=1):
    entities=[{'id':4,'x':float(x),'y':4.0,'health':health,'team':team}] if include else []
    entities.append({'id':8,'x':10.0,'y':11.0,'health':80})
    return {'pid':pid,'entities':entities}


def test_three_observations_produce_bounded_tracks(tmp_path):
    service,_,project,_=instance(tmp_path)
    report=analyze_entity_timeline([frame(1),frame(2,health=90),frame(4,health=70)])
    assert report['assessment']=='changes_observed'
    track=next(t for t in report['tracks'] if t['id']==4)
    assert track['travel_xy']==3
    assert track['net_health_change']==-30
    assert report['continuous_tracks']==2
    assert all(len(sha)==64 for sha in report['frame_hashes'])
    case=service.create_timeline(project,[frame(1),frame(2),frame(4)])
    assert service.get(project,case['id'])['evidence_current']
    assert service.summary(project)['case_count']==1
    assert 'entities' not in str(service.export(project))


def test_identity_warnings_for_pid_and_gap():
    frames=[frame(1),frame(2,pid=102,include=False),frame(5)]
    report=analyze_entity_timeline(frames)
    assert report['assessment']=='requires_identity_review'
    assert report['process_changed']
    first=next(t for t in report['tracks'] if t['id']==4)
    assert first['gaps']==1 and first['adjacent_pairs']==0
    assert first['identity_warning']


def test_reject_fabricated_or_unbounded_observations():
    for frames in ([],[frame(),frame()], [frame()]*13, 'bad'):
        with pytest.raises(ValueError): analyze_entity_timeline(frames)
    for broken in (frame(),frame(),frame()):
        bad=copy.deepcopy(broken)
        bad['entities'][0]['x']=float('nan')
        with pytest.raises(ValueError): analyze_entity_timeline([frame(),frame(),bad])
    bad=frame();bad['entities'].append(dict(bad['entities'][0]))
    with pytest.raises(ValueError):analyze_entity_timeline([frame(),frame(),bad])


def test_project_isolation_and_case_lifecycle(tmp_path):
    service,_,one,two=instance(tmp_path)
    case=service.create_timeline(one,[frame(1),frame(2),frame(3)], 'Known lab')
    with pytest.raises(ValueError):service.get(two,case['id'])
    with pytest.raises(ValueError):service.refresh(two,case['id'])
    with pytest.raises(ValueError):service.set_state(two,case['id'],'closed')
    assert not service.list(two)
    assert service.set_state(one,case['id'],'reviewed')['state']=='reviewed'
    assert service.set_state(one,case['id'],'closed')['state']=='closed'
    with pytest.raises(ValueError):service.set_state(one,case['id'],'verified_everywhere')
    source=service.store.get('p10_timeline',case['source_id'])
    service.store.put('p10_timeline',{**source,'report':{**source['report'],'assessment':'modified'}},id=source['id'])
    assert service.get(one,case['id'])['evidence_state']=='stale'
    assert service.refresh(one,case['id'])['evidence_state']=='stale'


def test_p9_cases_recheck_corroboration(tmp_path):
    service,p9,one,two=instance(tmp_path)
    review=p9.compare_game(one,frame(1),frame(2))
    case=service.create_from_review(one,review['id'])
    assert case['source_hash'] and case['evidence_level']=='decoded_observation_only'
    assert len(case['next_actions'])==2
    with pytest.raises(ValueError):service.create_from_review(two,review['id'])
    original=service.store.get('p9_evidence_review',review['id'])
    service.store.delete('p9_evidence_review',review['id'])
    assert not service.get(one,case['id'])['evidence_current']
    assert service.list(one)[0]['evidence_state']=='stale'


def test_staging_case_does_not_claim_unconfirmed_receipt(tmp_path):
    service,_,project,_=instance(tmp_path)
    review=service.store.put('p9_evidence_review',{'project_id':project,'report':{'type':'staging_quote_evidence',
        'assessment':'staging_price_invariants_passed', 'receipts_correlated':False,
        'verification_level':'staging_quote_only'}})
    case=service.create_from_review(project,review['id'])
    assert case['evidence_state']=='unverified'
    assert 'gateway' in case['next_actions'][0]


def test_app_dispatch_and_model_tool_read_only(tmp_path):
    from bughunter.model_tools import ModelTools, TOOL_SPECS, STUDY_TOOL_NAMES
    service,_,project,_=instance(tmp_path)
    mock=SimpleNamespace(store=service.store,investigation_p10=service)
    run=service.store.put('run', {'project_id':project,'status':'running'})
    agent=ModelTools(mock,run['id'],study=True)
    assert 'get_evidence_casebook' in STUDY_TOOL_NAMES
    assert 'investigation.p10.create_timeline' not in {t['name'] for t in TOOL_SPECS}
    assert agent.call('get_evidence_casebook',{})['phase']=='P10'
    assert service.dispatch('investigation.p10.export', {'project_id':project})['schema']=='bughunter.p10.casebook.v1'
    with pytest.raises(ValueError): service.dispatch('investigation.p10.run_unapproved', {'project_id':project})


def test_emergency_stop_blocks_mutation(tmp_path):
    service,_,project,_=instance(tmp_path)
    service.store.set_setting('emergency_stop',True)
    with pytest.raises(ValueError,match='Emergency'):
        service.create_timeline(project,[frame(1),frame(2),frame(3)])


def test_pid_boundary_never_counts_cross_process_movement():
    report=analyze_entity_timeline([frame(1,pid=111),frame(900,pid=222),frame(2,pid=222)])
    player=next(t for t in report['tracks'] if t['id']==4)
    assert player['pid_discontinuities']==1
    assert player['travel_xy']==898
    assert player['largest_step_xy']==898
    assert report['assessment']=='requires_identity_review'


def test_health_change_detected_even_if_net_zero():
    report=analyze_entity_timeline([frame(1,health=100),frame(1,health=80),frame(1,health=100)])
    assert report['health_change_tracks']==1
    assert report['assessment']=='changes_observed'
    assert next(t for t in report['tracks'] if t['id']==4)['health_change_events']==2
