import pytest
from bughunter.app import Application
from bughunter.operator_tools import OperatorTools
from bughunter import harness_modes

@pytest.fixture
def app(tmp_path,monkeypatch):
 a=Application(tmp_path,primary=False);monkeypatch.setattr(a.operator,'_loop',lambda *args:None)
 yield a
 a.close()

def test_per_model_profiles_are_isolated_and_persist(app):
 harness_modes.save_profile(app.store,'codex','large','full',False)
 assert harness_modes.profile(app.store,'codex','large')=={'execution_mode':'full','background':False}
 assert harness_modes.profile(app.store,'ollama','small')['execution_mode']=='auto'

def test_plan_enforces_read_only_at_host(app):
 task=app.harness.start('Inspect sources and plan changes',execution_mode='plan');tools=OperatorTools(app,task['run_id'])
 assert tools.app_action('sources.list')==[]
 for name,args in [('file_write',{'path':'/tmp/no','content':'no'}),('install_tool',{'package':'adb'}),('app_action',{'method':'sources.import','params':{}}),('desktop_call',{'name':'desktop_click','arguments':{}})]:
  with pytest.raises(PermissionError):getattr(tools,name)(**args)

def test_full_grants_once_and_background_is_default(app):
 task=app.harness.start('Task',grants=['app'],execution_mode='full');row=app.store.get('harness_session',task['id'])
 assert set(row['grants'])=={'app','files','computer','shell','install','integrations'}
 assert row['background'] is True

def test_invalid_mode_never_starts_task(app):
 with pytest.raises(ValueError):app.harness.start('Task',execution_mode='unrestricted-guess')
 assert app.store.list('harness_session')==[]

def test_registry_search_returns_exact_package_id_then_validated_plan(app,monkeypatch):
 from bughunter import tool_installer as module
 monkeypatch.setattr(module.platform,'system',lambda:'Darwin');monkeypatch.setattr(module,'executable',lambda name:'/fake/brew' if name=='brew' else None)
 def run(argv,**kw):return {'stdout':'firefox\n' if 'search' in argv else '', 'stderr':'','exit_code':0}
 monkeypatch.setattr(module.process_runtime,'run',run)
 results=app.tool_installer.search('firefox');assert results['items']
 package=results['items'][-1]['id'];plan=app.tool_installer.plan(package)
 assert plan['argv']==['/fake/brew','install','--cask','firefox']
 with pytest.raises(ValueError):app.tool_installer.plan('--arbitrary-script')
 app.store.put('registry_package',{'platform':'Darwin','identifier':'--bad','label':'bad','binaries':[]},id='registry_bad')
 with pytest.raises(ValueError):app.tool_installer.plan('registry_bad')

def test_agent_preferences_support_all_chats_model_and_private_chat(app):
 saved=harness_modes.save_preferences(app.store,provider='codex',model='large',scope='all',execution_mode='auto',background=True,web_search_mode='off',grants=['app'])
 assert saved['permissions_configured'] and saved['web_search_mode']=='off'
 single=harness_modes.save_preferences(app.store,scope='chat',execution_mode='plan',web_search_mode='auto',grants=['app'])
 assert single['chat_id'] and single['execution_mode']=='plan'
 assert harness_modes.preferences(app.store,'ollama','tiny')['execution_mode']=='auto'
 assert harness_modes.preferences(app.store,'codex','large',single['chat_id'])['execution_mode']=='plan'
 harness_modes.save_preferences(app.store,provider='ollama',model='tiny',scope='model',execution_mode='auto',background=False,grants=['app'])
 assert not harness_modes.preferences(app.store,'ollama','tiny')['background']
 assert harness_modes.preferences(app.store,'ollama','other')['background']

def test_search_auto_and_off_are_enforced_by_host(app,monkeypatch):
 assert harness_modes.should_search('Look this up online','auto')
 assert harness_modes.should_search('Latest release notes','auto')
 assert not harness_modes.should_search('Explain this code','auto')
 assert not harness_modes.should_search('Search the web','off')
 task=app.harness.start('Search for sources',['app'],web_search_mode='off');tools=OperatorTools(app,task['run_id'])
 called=[];monkeypatch.setattr(app.research,'search',lambda params:called.append(params))
 with pytest.raises(PermissionError):tools.web_search('example')
 assert called==[]

def test_saved_access_also_gates_ordinary_learning_chat_tools(app):
 from bughunter.model_tools import ModelTools
 run=app.store.put('run',{'kind':'agent','status':'running','project_id':None,'agent_settings':{'permissions_configured':True,'execution_mode':'auto','grants':['app']}})
 tools=ModelTools(app,run['id'])
 with pytest.raises(PermissionError,match='shell'):tools.call('op_shell',{'command':'must not run'})

def test_always_search_fetches_sources_before_start_and_followup(app,monkeypatch):
 calls=[]
 monkeypatch.setattr(app.research,'search',lambda params:calls.append(params) or {'results':[{'title':'Fixture','url':'https://example.test/source','snippet':'Observed source excerpt'}]})
 task=app.harness.start('Explain this note',['app'],web_search_mode='on')
 assert len(calls)==1
 run=app.store.get('operator_run',task['run_id']);assert 'https://example.test/source' in str(run['transcript'])
 run['status']='awaiting_user';app.store.put('operator_run',run,id=run['id'])
 app.harness.send(task['id'],'Explain another note');assert len(calls)==2
