"""Real persistence and isolation checks, with fake external runners."""
from types import SimpleNamespace
import pytest

from bughunter.store import Store
from bughunter.tasks import TaskService
from bughunter.orchestrator import Orchestrator
from bughunter.skills import SkillService


class KnowledgeStub:
    def __init__(self):self.calls=[]
    def search_for_project(self, query, project_id, limit=5):
        self.calls.append((query,project_id,limit))
        return [{'id':'chunk_1','source_id':'source_shared','title':'Shared reference'}]


class TaskStub:
    def __init__(self):self.started=[];self.cancelled=[];self.status='running'
    def start(self, **options):
        self.started.append(options)
        return {'id':'recorded_task_1', 'status':'running'}
    def cancel(self, task_id):self.cancelled.append(task_id)
    def dispatch(self, method, params):return {'id':params['id'],'status':self.status}


def make(tmp_path):
    store=Store(tmp_path/'data')
    project=store.put('project',{'name':'Project A', 'scope':['https://test.example'], 'authorization':'Local demo app', 'workspace_only':False})
    app=SimpleNamespace(store=store, knowledge=KnowledgeStub(), tasks=TaskStub())
    plan=Orchestrator(app)
    return store, project, app, plan


def test_research_plan_does_not_start_tools_and_reuses_checkpoints(tmp_path):
    store,project,app,plan=make(tmp_path)
    row=plan.create('Review project lessons and prerequisites',project['id'])
    assert row['status']=='draft'
    result=plan.run(row['id'])
    assert result['status']=='completed'
    assert all(s['status']=='completed' for s in result['steps'])
    assert app.knowledge.calls==[('Review project lessons and prerequisites',project['id'],5)]
    assert result['steps'][1]['result']['references'][0]['source_id']=='source_shared'
    assert app.tasks.started==[]
    with pytest.raises(ValueError):plan.run(row['id'])
    restarted=Orchestrator(app)
    assert restarted.get(row['id'])['status']=='completed'


def test_explicit_approval_before_running_external_agents(tmp_path):
    store,project,app,plan=make(tmp_path)
    row=plan.create('Review my repository safely',project['id'],'code_review')
    assert plan.run(row['id'])['status']=='awaiting_approval'
    assert app.tasks.started==[]
    with pytest.raises(ValueError):plan.approve(row['id'], ['claude'], '') if store.set_setting('emergency_stop',True) else None
    store.set_setting('emergency_stop',False)
    started=plan.approve(row['id'], ['claude','codex'], '/tmp/demo')
    assert started['status']=='running'
    assert started['steps'][-1]['task_id']=='recorded_task_1'
    assert app.tasks.started[0]['mode']=='read-only'
    assert app.tasks.started[0]['agents']==['claude','codex']
    with pytest.raises(ValueError):plan.approve(row['id'], ['claude'], '')
    app.tasks.status='completed'
    assert plan.get(row['id'])['status']=='completed'


def test_web_requires_authorized_scope_and_no_auto_replay(tmp_path):
    store,project,app,plan=make(tmp_path)
    limited=store.put('project',{'name':'Research only','workspace_only':True,'scope':[]})
    row=plan.create('Review login access controls',limited['id'],'web_review')
    assert plan.run(row['id'])['status']=='awaiting_approval'
    with pytest.raises(ValueError,match='authorized project'):
        plan.approve(row['id'])
    assert app.tasks.started==[]
    row2=plan.create('Review login access controls',project['id'],'web_review')
    plan.run(row2['id'])
    plan.approve(row2['id'])
    restarted=Orchestrator(app)
    assert restarted.get(row2['id'])['status']=='interrupted'
    assert len(app.tasks.started)==1
    with pytest.raises(ValueError, match='previous external step'):
        restarted.run(row2['id'])


def test_cancel_plan_cancels_linked_runner(tmp_path):
    store,project,app,plan=make(tmp_path)
    row=plan.create('Review source for problems',project['id'],'code_review')
    plan.run(row['id']); plan.approve(row['id'],['codex'])
    assert plan.cancel(row['id'])['status']=='cancelled'
    assert app.tasks.cancelled==['recorded_task_1']


def test_only_verified_methods_are_retrieved_and_projects_are_isolated(tmp_path):
    store,project,app,plan=make(tmp_path)
    other=store.put('project',{'name':'Project B'})
    evidence=store.put('work_task',{'title':'Review run','project_id':project['id'], 'status':'completed'})
    service=SkillService(store)
    skill=service.propose('Checking local headers','Compare trusted and test request headers to validate a configuration finding.',evidence['id'],project['id'],['headers','web'])
    assert service.match('headers',project['id'])==[]
    with pytest.raises(ValueError, match='checked'):
        service.review(skill['id'],True,'ok')
    service.review(skill['id'],True,'I compared the recorded requests against a clean baseline.')
    assert len(service.match('headers',project['id']))==1
    assert service.match('headers',other['id'])==[]
    with pytest.raises(ValueError,match='different project'):
        service.propose('Wrong project scope','Please study the header differences in the sample.',evidence['id'],other['id'])
    assert service.review(skill['id'],False,'Recheck the evidence')['status']=='rejected'
    assert service.match('headers',project['id'])==[]
