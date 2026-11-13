import json
import sys
import threading
import time
import pytest
from bughunter.app import Application
from bughunter.operator_tools import OperatorTools
from bughunter.harness import HarnessService
from bughunter import process_runtime
from bughunter.tool_installer import ToolInstaller

@pytest.fixture
def app(tmp_path):
 a=Application(tmp_path,primary=False)
 yield a
 a.close()


def wait_for(fn):
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  result=fn()
  if result:return result
  time.sleep(.02)
 raise AssertionError('Expected task state did not arrive.')

def test_desktop_catalog_does_not_fail_on_macos_ghidra_path_check(app,monkeypatch):
 monkeypatch.setattr('bughunter.tool_installer.platform.system',lambda:'Darwin')
 monkeypatch.setattr('bughunter.tool_installer.executable',lambda _:None)
 monkeypatch.setattr('bughunter.tool_installer.Path.is_dir',lambda _:False)
 assert app.tool_installer.installed('ghidra') is False


def test_ui_task_to_tool_to_one_saved_assistant_reply(app,monkeypatch):
 calls=[]
 responses=iter([
  {'message':{'role':'assistant','content':'','tool_calls':[{'id':'tc_one','type':'function','function':{'name':'app_action','arguments':'{"method":"sources.list"}'}}]},'tool_calls':[{'id':'tc_one','name':'app_action','input':{'method':'sources.list'}}]},
  {'message':{'role':'assistant','content':'The saved source is ready.'},'tool_calls':[]},
 ])
 def turn(**kw):calls.append(kw);return next(responses)
 monkeypatch.setattr('bughunter.provider_api.single_tool_turn',turn)
 called=[]
 app.harness.actions.call=lambda method,params,**kwargs:called.append(method) or {'id':'source_fixture','status':'ready'}
 started=app.dispatch('harness.start',{'message':'Check this source','grants':['app']})
 record=wait_for(lambda: (r if (r:=app.dispatch('harness.get',{'id':started['id']}))['status']=='awaiting_user' else None))
 assert called==['sources.list']
 assert [m['content'] for m in record['session']['messages']]==['Check this source','The saved source is ready.']
 assert not any(m.get('role') in {'system','tool'} for m in record['session']['messages'])
 assert len(record['activity'])==2
 assert all(s['name'] in {'app_action','app_features','assets_query','delegate_task','worker_status','remember_context','web_search','library_sources','library_search','library_read','agent_skills','agent_skill_read','rea_status'} for s in calls[0]['tool_specs'])
 assert app.dispatch('chat.get',{'id':record['session']['id']})['harness_id']==record['id']
 # A follow-up resumes the actual worker rather than setting running without a thread.
 monkeypatch.setattr('bughunter.provider_api.single_tool_turn',lambda **kw:{'message':{'role':'assistant','content':'Here is the citation.'},'tool_calls':[]})
 app.dispatch('harness.send',{'id':record['id'],'message':'Show the citation'})
 final=wait_for(lambda:(r if (r:=app.harness.get(record['id']))['status']=='awaiting_user' and len(r['session']['messages'])==4 else None))
 assert final['session']['messages'][-1]['content']=='Here is the citation.'


def test_task_capabilities_enforced_at_execution_and_unknown_rpcs_rejected(app,monkeypatch):
 monkeypatch.setattr(app.operator,'_loop',lambda *args:None)
 record=app.harness.start('Read app context',['app'])
 tools=OperatorTools(app,record['run_id'])
 with pytest.raises(PermissionError):tools.shell(command='echo should-not-run')
 with pytest.raises(ValueError):tools.app_action('settings.secret',{'value':'never'})
 with pytest.raises(ValueError):tools.app_action('system.resume',{})
 with pytest.raises(ValueError):app.harness.start('No task permissions',[])


def test_stop_all_cancels_owned_shell_and_revokes_old_session(app,monkeypatch,tmp_path):
 marker=tmp_path/'started'
 def turn(**kw):
  return {'message':{'role':'assistant','content':''},'tool_calls':[{'id':'tc','name':'shell','input':{'command':__import__('shlex').join([sys.executable,'-c',f"from pathlib import Path;import time;Path({str(marker)!r}).write_text('started');time.sleep(90)"])}}]}
 monkeypatch.setattr('bughunter.provider_api.single_tool_turn',turn)
 record=app.harness.start('Run a wait command',['shell'])
 wait_for(marker.exists)
 start=time.monotonic();app.stop_all()
 wait_for(lambda:not app.operator._runs[record['run_id']].is_alive())
 assert time.monotonic()-start<5
 assert app.harness.get(record['id'])['status']=='cancelled'
 app.dispatch('system.resume',{})
 with pytest.raises(InterruptedError):app.harness.send(record['id'],'continue')
 assert not process_runtime._ACTIVE


def test_primary_recovery_keeps_receipts_and_does_not_replay_actions(app,monkeypatch):
 monkeypatch.setattr(app.operator,'_loop',lambda *args:None)
 record=app.harness.start('Observe an app',['app'])
 run=app.store.get('operator_run',record['run_id']);run['transcript'].append({'role':'tool','name':'app_action','content':'saved evidence'})
 app.store.put('operator_run',run,id=run['id'])
 recovered=HarnessService(app,recover=True).get(record['id'])
 assert recovered['status']=='interrupted'
 assert recovered['activity'][0]['result']=='saved evidence'


@pytest.mark.parametrize('system,manager,package,expected',[
 ('Darwin','/fake/brew','scrcpy',['/fake/brew','install','scrcpy']),
 ('Windows','/fake/winget','adb',['/fake/winget','install','--exact','--id','Google.PlatformTools']),
 ('Linux','/fake/apt-get','adb',['/fake/sudo','-n','/fake/apt-get','install','-y','adb']),
])
def test_platform_install_plans_are_argv_not_shell(app,monkeypatch,system,manager,package,expected):
 from bughunter import tool_installer as module
 name=manager.rsplit('/',1)[-1]
 monkeypatch.setattr(module.platform,'system',lambda:system)
 monkeypatch.setattr(module,'executable',lambda binary:manager if binary==name else '/fake/sudo' if binary=='sudo' else None)
 if system=='Linux':monkeypatch.setattr(module.os,'geteuid',lambda:1000)
 assert app.tool_installer.plan(package)['argv'][:len(expected)]==expected


def test_codex_harness_attaches_native_tools_and_persists_their_receipts(app,monkeypatch):
 app.store.set_setting('settings',{'provider':'codex','model':'fixture-native','reasoning_effort':'medium'})
 class Session:
  def __init__(self,**kwargs):pass
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def run(self,model,system,prompt,**kwargs):
   assert 'JSON is an application RPC' not in system
   assert {s['name'] for s in kwargs['specs']}=={'app_features','app_action','assets_query','delegate_task','worker_status','remember_context','web_search','library_sources','library_search','library_read','agent_skills','agent_skill_read','rea_status'}
   assert kwargs['final_only'] is True
   result=kwargs['tool_handler']('app_action',{'method':'sources.list'})
   assert isinstance(result,list)
   return 'Native tools returned the source list.'
 monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
 started=app.harness.start('Find my sources',['app'])
 record=wait_for(lambda:(r if (r:=app.harness.get(started['id']))['status']=='awaiting_user' else None))
 assert record['session']['messages'][-1]['content']=='Native tools returned the source list.'
 assert [item['name'] for item in record['activity']]==['app_action','app_action']


def test_windows_npm_shims_use_node_without_cmd_interpreting_prompt_text(tmp_path,monkeypatch):
 script=tmp_path/'node_modules/fixture/bin/cli.js';script.parent.mkdir(parents=True);script.write_text('// fixture')
 wrapper=tmp_path/'fixture.cmd';wrapper.write_text('@"%_prog%" "%dp0%\\node_modules\\fixture\\bin\\cli.js" %*')
 monkeypatch.setattr(process_runtime.shutil,'which',lambda name:'C:/Node/node.exe')
 prompt='Text with & | $(literal) and "quotes"'
 argv=process_runtime.cli_argv([str(wrapper),'--system-prompt',prompt],windows=True)
 assert argv==['C:/Node/node.exe',str(script),'--system-prompt',prompt]
 assert 'cmd.exe' not in argv


def test_harness_keeps_project_and_attachment_scope(app,monkeypatch):
 monkeypatch.setattr(app.operator,'_loop',lambda *args:None)
 app.store.put('project',{'name':'A'},id='project_a');app.store.put('project',{'name':'B'},id='project_b')
 app.store.put('source',{'project_id':'project_b'},id='source_b')
 with pytest.raises(ValueError,match='unavailable'):
  app.harness.start('Read another project',['app'],project_id='project_a',source_ids=['source_b'])
 record=app.harness.start('Check current sources',['app'],project_id='project_a')
 tools=OperatorTools(app,record['run_id'])
 with pytest.raises(ValueError,match='another project'):tools.app_action('sources.list',{'project_id':'project_b'})
 with pytest.raises(ValueError,match='unavailable'):tools.app_action('sources.get',{'id':'source_b'})


def test_app_action_catalog_pages_without_hiding_later_actions(app):
 first=app.harness.actions.catalog()
 assert len(first['items'])==20 and first['next_offset']==20
 second=app.harness.actions.catalog(offset=20)
 assert not {r['method'] for r in first['items']}&{r['method'] for r in second['items']}
 assert any(r['method']=='system.apps' for r in app.harness.actions.catalog('system.apps')['items'])
 # Authorization checks the queried catalog, not only its first page.
 assert app.harness.actions.call('system.apps',{'query':'not-installed-fixture'})['items']==[]
