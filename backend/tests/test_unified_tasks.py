from types import SimpleNamespace
from threading import Event
import time
from bughunter.tasks import TaskService
from bughunter.store import Store


class FakeAgents:
    def __init__(self,store):self.store=store
    def start(self,**kw):return self.store.put('agent_job',{'status':'starting','workers':{},'prompt':kw['prompt'], 'agents':kw['agents']})
    def cancel(self,id):
        j=self.store.get('agent_job',id)
        self.store.put('agent_job',{**j,'status':'cancelled'},id=id)


class FakeRuns:
    def __init__(self,store):self.store=store
    def start(self,project_id,prompt):return self.store.put('run',{'status':'awaiting_approval','project_id':project_id,'prompt':prompt})
    def start_study(self,source_id):return self.store.put('run',{'status':'running','kind':'study','source_id':source_id})
    def stop(self,id):
        r=self.store.get('run',id);self.store.put('run',{**r,'status':'cancelled'},id=id)


class FakeTor:
    def collect(self,source_id,max_pages,max_depth,cancel_check=None):
        return {'pages':[{'id':'page-1'}], 'errors':[],'cancelled':bool(cancel_check and cancel_check())}


def app_fixture(tmp_path):
    store=Store(tmp_path/'data')
    return SimpleNamespace(store=store,agent_jobs=FakeAgents(store),runs=FakeRuns(store),tor=FakeTor())


def test_ledger_covers_agent_web_study_memory_and_tor(tmp_path):
    app=app_fixture(tmp_path)
    app.store.put('project',{'name':'Test project'},id='project_1')
    app.store.put('tor_source',{'title':'Research'},id='source_1')
    svc=TaskService(app)
    a=svc.start(kind='coding_agents',prompt='Review files',agents=['claude'],project_id='project_1')
    w=svc.start(kind='web_investigation',prompt='Check headers',project_id='project_1')
    s=svc.start(kind='study',source_id='doc_1',project_id='project_1')
    m=svc.start(kind='memory_handoff',prompt='Inspect local test process',project_id='project_1')
    t=svc.start(kind='tor_capture',source_id='source_1',max_pages=1,max_depth=0,project_id='project_1')
    for _ in range(200):
        if svc.dispatch('tasks.get',{'id':t['id']})['status']=='completed':break
        time.sleep(0.01)
    assert svc.dispatch('tasks.get',{'id':w['id']})['status']=='awaiting_approval'
    assert svc.dispatch('tasks.get',{'id':m['id']})['status']=='awaiting_workspace'
    assert svc.dispatch('tasks.get',{'id':t['id']})['status']=='completed'
    assert {r['kind'] for r in svc.dispatch('tasks.list',{'project_id':'project_1'})}=={'coding_agents','web_investigation','study','memory_handoff','tor_capture'}
    assert svc.dispatch('tasks.cancel',{'id':a['id']})['status']=='cancelled'
    assert svc.dispatch('tasks.cancel',{'id':m['id']})['status']=='cancelled'
