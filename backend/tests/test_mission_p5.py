"""P5 bounded orchestration and immutable experiment evidence contracts."""
import base64
import time
from types import SimpleNamespace

import pytest

from bughunter.store import Store
from bughunter.knowledge import KnowledgeService
from bughunter.learning_p4 import LearningP4
from bughunter.mission_p5 import MissionP5


@pytest.fixture
def env(tmp_path, monkeypatch):
    store=Store(tmp_path/'state')
    knowledge=KnowledgeService(store,start_worker=False)
    monkeypatch.setattr(knowledge,'_index_vectors', lambda source:'skip')
    app=SimpleNamespace(store=store,knowledge=knowledge)
    app.learning_p4=LearningP4(app)
    app.missions_p5=MissionP5(app)
    yield app
    knowledge.shutdown()


def lesson(app, text='Step 1: Find the health value in an offline game.\nStep 2: Verify the pointer chain after restarting.'):
    source=app.knowledge.import_source(filename='lesson.md',content_base64=base64.b64encode(text.encode()).decode())
    app.knowledge.process_source(source['id'])
    return source,app.knowledge.procedures.list(source_id=source['id'])[0]


def finished(app,id,seconds=8):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=app.knowledge.experiments.get(id)
        if result['status'] not in {'queued','running','stopping'}:return result
        time.sleep(.02)
    raise AssertionError('Timed out waiting for a fixed test recipe.')


def test_missing_evidence_mission_is_a_review_not_a_claim(env):
    m=env.missions_p5.create(goal='Understand an unknown topic')
    assert m['status']=='ready'
    assert m['next']['type']=='human_review'
    after=env.missions_p5.advance(m['id'])
    assert after['status']=='needs_review'
    assert not after['results']
    assert len(env.store.list('experiment_run'))==0


def test_source_citation_one_at_a_time_and_repeat_safe(env):
    source,p=lesson(env)
    mission=env.missions_p5.create(goal='Find offline game health values and pointer chains',)
    assert mission['next']['type']=='citation_check'
    m=env.missions_p5.advance(mission['id'])
    assert m['citation_checks_started']==1 and m['pending_run_id']
    assert m['next']['type']=='wait'
    finished(env,m['pending_run_id'])
    resolved=env.missions_p5.advance(m['id'])
    assert not resolved['pending_run_id']
    assert len(resolved['results'])==1
    assert resolved['results'][0]['verification_level']=='source_verified_only'
    assert resolved['results'][0]['status']=='passed'
    assert 'target_verified' not in str(resolved)
    after=env.missions_p5.advance(m['id'])
    assert after['citation_checks_started']==1
    assert after['next']['type'] in {'lab_approval','human_review'}
    assert len(env.knowledge.experiments.list(procedure_id=p['id']))==1


def test_lab_requires_manual_confirm_and_allowed_fixed_recipe(env,monkeypatch):
    source,p=lesson(env)
    m=env.missions_p5.create(goal='Find offline game health values and pointer chains')
    run=env.missions_p5.advance(m['id'])
    finished(env,run['pending_run_id'])
    env.missions_p5.advance(m['id'])
    assert env.missions_p5.advance(m['id'])['next']['type']=='lab_approval'
    with pytest.raises(ValueError,match='explicit desktop confirmation'):
        env.missions_p5.approve_lab(m['id'],p['id'],'memory_restart_lab')
    with pytest.raises(ValueError,match='Unsupported'):
        env.missions_p5.approve_lab(m['id'],p['id'],'arbitrary_shell',confirm=True)
    # Avoid expensive spawned subprocess here. P1 has integration tests for real fixture execution.
    def fake_start(proc,recipe,approve_lab=False):
        assert approve_lab and proc==p['id'] and recipe=='memory_restart_lab'
        return env.store.put('experiment_run',{'procedure_id':proc,'recipe':recipe,'status':'queued','verification_level':'not_tested'})
    monkeypatch.setattr(env.knowledge.experiments,'start',fake_start)
    approved=env.missions_p5.approve_lab(m['id'],p['id'],'memory_restart_lab',confirm=True)
    assert approved['labs_started']==1 and approved['pending_run_id']
    assert any(e['kind']=='operator_approved_lab' for e in approved['events'])
    with pytest.raises(ValueError,match='cannot start a lab'):
        env.missions_p5.approve_lab(m['id'],p['id'],'memory_restart_lab',confirm=True)


def test_project_scoping_and_archived_plan(env):
    a=env.store.put('project',{'name':'One'})['id']
    b=env.store.put('project',{'name':'Two'})['id']
    mission=env.missions_p5.create(goal='Inspect archive history',project_id=a)
    assert len(env.missions_p5.list(a))==1
    assert len(env.missions_p5.list(b))==0
    with pytest.raises(ValueError):env.missions_p5.get(mission['id'],b)
    with pytest.raises(ValueError):env.missions_p5.advance(mission['id'],b)
    env.learning_p4.archive(mission['plan_id'],True,project_id=a)
    with pytest.raises(ValueError,match='Restore'):env.missions_p5.advance(mission['id'],a)


def test_source_deletion_detects_drift_before_execution(env):
    source,p=lesson(env)
    m=env.missions_p5.create(goal='Find offline game health values and pointer chains')
    assert m['next']['type']=='citation_check'
    env.knowledge.delete_source(source['id'])
    after=env.missions_p5.advance(m['id'])
    assert after['status']=='stale'
    assert len(env.store.list('experiment_run'))==0
    with pytest.raises(ValueError):env.missions_p5.advance(m['id'])


def test_failed_result_requires_review_and_stopping_is_durable(env,monkeypatch):
    source,p=lesson(env)
    m=env.missions_p5.create(goal='Find offline game health values and pointer chains')
    def fake_start(proc,recipe,approve_lab=False):
        return env.store.put('experiment_run',{'procedure_id':proc,'recipe':recipe,'status':'queued','verification_level':'not_tested'})
    monkeypatch.setattr(env.knowledge.experiments,'start',fake_start)
    first=env.missions_p5.advance(m['id'])
    env.store.put('experiment_run',{**env.store.get('experiment_run',first['pending_run_id']),
                 'status':'failed','error':'source mismatch'},id=first['pending_run_id'])
    second=env.missions_p5.advance(m['id'])
    assert second['status']=='needs_review' and second['stage']=='failed_experiment'
    assert second['results'][0]['status']=='failed'
    assert env.missions_p5.stop(m['id'])['status']=='stopped'
    assert MissionP5(env).get(m['id'])['status']=='stopped'


def test_non_owner_model_cannot_call_desktop_lab(monkeypatch,env):
    from bughunter.model_tools import STUDY_TOOL_NAMES,TOOL_SPECS
    names={s['name'] for s in TOOL_SPECS}
    for tool in ('list_study_missions','inspect_study_mission','advance_study_mission'):
        assert tool in names and tool in STUDY_TOOL_NAMES
    assert 'approve_lab' not in names
    assert 'mission.p5.approve_lab' not in names


def test_invalid_inputs_and_operation_dispatch(env):
    with pytest.raises(ValueError):env.missions_p5.create()
    with pytest.raises(ValueError):env.missions_p5.create(goal='Hello world',plan_id='fake')
    with pytest.raises(ValueError):env.missions_p5.dispatch('mission.p5.execute_shell',{})
    m=env.missions_p5.dispatch('mission.p5.create',{'goal':'Study an unfamiliar topic'})
    assert m['id']
    assert env.missions_p5.dispatch('mission.p5.get',{'id':m['id']})['id']==m['id']
    assert env.missions_p5.dispatch('mission.p5.list',{})[0]['id']==m['id']


def test_procedure_quote_changed_marks_session_stale(env):
    source,procedure=lesson(env)
    mission=env.missions_p5.create(goal='Find offline game health values and pointer chains')
    old=env.store.get('course_procedure',procedure['id'])
    altered={**old,'steps':[dict(old['steps'][0],quote='invented quote'),*old['steps'][1:]]}
    env.store.put('course_procedure',altered,id=old['id'])
    changed=env.missions_p5.advance(mission['id'])
    assert changed['status']=='stale'
    assert env.store.list('experiment_run')==[]


def test_operator_retry_after_failed_source_check(env,monkeypatch):
    source,procedure=lesson(env)
    m=env.missions_p5.create(goal='Find offline game health values and pointer chains')
    created=[]
    def fake_start(proc,recipe,approve_lab=False):
        run=env.store.put('experiment_run',{'procedure_id':proc,'recipe':recipe,'status':'queued','verification_level':'not_tested'})
        created.append(run['id'])
        return run
    monkeypatch.setattr(env.knowledge.experiments,'start',fake_start)
    waiting=env.missions_p5.advance(m['id'])
    run=env.store.get('experiment_run',waiting['pending_run_id'])
    env.store.put('experiment_run',{**run,'status':'failed','error':'Quoted text was not found',
                                    'hypotheses':['Check original locator']},id=run['id'])
    reviewed=env.missions_p5.advance(m['id'])
    assert reviewed['next']['type']=='failure_review'
    assert len(created)==1
    assert 'Quoted text' in reviewed['results'][0]['error']
    assert env.missions_p5.advance(m['id'])['next']['type']=='failure_review'
    assert len(created)==1
    retried=env.missions_p5.retry_source(m['id'])
    assert retried['citation_checks_started']==2
    assert retried['pending_run_id']==created[-1] and len(created)==2
    with pytest.raises(ValueError):env.missions_p5.retry_source(m['id'])


def test_local_evidence_export_and_project_gate(env,tmp_path):
    project=env.store.put('project',{'name':'Test project'})
    m=env.missions_p5.create(goal='Study a missing subject',project_id=project['id'])
    with pytest.raises(ValueError):env.missions_p5.export(m['id'],project_id='not_a_project')
    result=env.missions_p5.export(m['id'],project_id=project['id'])
    import hashlib,json,os
    from pathlib import Path
    path=Path(result['path'])
    assert path.is_file() and path.parent==env.store.root/'exports'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==result['sha256']
    data=json.loads(path.read_text())
    assert data['format']=='bughunter.p5.study_mission.v1'
    assert data['status']=='ready' and data['project_id']==project['id']
    assert data['results']==[] and 'notes' in data
    assert os.stat(path).st_mode & 0o077 == 0


def test_model_study_tool_end_to_end_without_lab_authority(env):
    from bughunter.model_tools import ModelTools
    env.settings=SimpleNamespace(get=lambda:{'provider':'ollama'})
    env.tor=SimpleNamespace(settings=lambda:{'allow_remote_ai':False})
    source,p=lesson(env)
    run=env.store.put('run',{'kind':'study','project_id':None,'status':'running','source_id':source['id']})
    model=ModelTools(env,run['id'],study=True)
    created=model.call('create_study_mission',{'goal':'Find offline game health values and pointer chains'})
    assert created['id']
    assert model.call('list_study_missions',{})
    assert model.call('inspect_study_mission',{'id':created['id']})['id']==created['id']
    progress=model.call('advance_study_mission',{'id':created['id']})
    assert progress['pending_run_id']
    with pytest.raises(ValueError):model.call('mission.p5.approve_lab',{'id':created['id']})
