import threading,time
import pytest
from bughunter.app import Application

@pytest.fixture
def app(tmp_path,monkeypatch):
 a=Application(tmp_path,primary=False);monkeypatch.setattr(a.operator,'_loop',lambda *args:None)
 yield a
 a.close()

def wait(fn):
 deadline=time.monotonic()+4
 while time.monotonic()<deadline:
  if result:=fn():return result
  time.sleep(.02)
 raise AssertionError('worker state timeout')

def test_delegation_inherits_model_and_reduces_permissions(app,monkeypatch):
 app.store.set_setting('settings',{'provider':'codex','model':'parent-model'})
 task=app.harness.start('Research independent topics',execution_mode='full');app.store.set_setting('settings',{'provider':'ollama','model':'new-selection'})
 child=app.background_workers.start(task['id'],'Inspect saved sources')
 row=wait(lambda:app.store.get('background_worker',child['id']).get('harness_id'))
 session=app.store.get('harness_session',row);run=app.store.get('operator_run',session['run_id'])
 assert run['settings_snapshot']['model']=='parent-model'
 assert 'computer' not in session['grants'] and session['background']
 with pytest.raises(ValueError):app.background_workers.start(session['id'],'Recursive child')
 app.harness.stop(task['id']);wait(lambda:app.background_workers.get(child['id'])['status']=='cancelled')

def test_global_stop_cancels_queued_workers_and_does_not_resume_them(app):
 task=app.harness.start('Independent tasks',['app'])
 children=[app.background_workers.start(task['id'],f'Read source {i}') for i in range(3)]
 with pytest.raises(ValueError):app.background_workers.start(task['id'],'Fourth')
 app.stop_all()
 for child in children:wait(lambda:app.background_workers.get(child['id'])['status']=='cancelled')
 app.dispatch('system.resume',{})
 assert all(app.background_workers.get(c['id'])['status']=='cancelled' for c in children)

def test_restart_keeps_receipts_and_marks_workers_interrupted(app):
 from bughunter.background_workers import BackgroundWorkers
 row=app.store.put('background_worker',{'parent_id':'harness_old','status':'running','answer':'Saved observation'})
 BackgroundWorkers(app,recover=True)
 saved=app.store.get('background_worker',row['id']);assert saved['status']=='interrupted' and saved['answer']=='Saved observation'

def test_parent_collects_worker_results_once_without_model_polling(app,monkeypatch):
 from bughunter.operator_agent import OperatorAgent
 # The real parent loop receives a host-delivered result and produces one aggregate reply.
 parent=app.harness.start('Combine delegated observations',['app'])
 app.store.put('background_worker',{'parent_id':parent['id'],'prompt':'Inspect docs','status':'completed','answer':'Observed cited result'})
 replies=[]
 def turn(**kwargs):
  replies.append(kwargs['transcript'])
  return {'message':{'role':'assistant','content':'Combined observed result' if len(replies)>1 else 'Draft before workers'},'tool_calls':[]}
 monkeypatch.setattr('bughunter.provider_api.single_tool_turn',turn)
 OperatorAgent._loop(app.operator,parent['run_id'],threading.Event())
 result=app.harness.get(parent['id'])
 assert result['status']=='awaiting_user' and len(replies)==2
 assert 'Observed cited result' in str(replies[1])
 assert 'Draft before workers' not in str(result['session']['messages'])
 assert result['session']['messages'][-1]['content']=='Combined observed result'
