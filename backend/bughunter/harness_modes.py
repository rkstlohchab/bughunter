"""Execution choices shared by cloud/local models; providers never grant host permissions."""
MODES={
 'plan':{'label':'Plan','detail':'Inspect and explain a plan. No changes or installations.','grants':['app','computer','files','integrations']},
 'auto':{'label':'Auto','detail':'Work within the task access you chose. Background steps first.','grants':['app','computer']},
 'full':{'label':'Full access','detail':'Allow app, computer, file, command and installation tools for this task. OS permissions still apply.','grants':['app','computer','files','shell','install','integrations']},
}
READ_APP={'system.apps','system.status','sources.list','sources.get','sources.coverage','knowledge.search','knowledge.graph','knowledge.status','knowledge.integrity','knowledge.frames','knowledge.learnings','knowledge.procedures','knowledge.procedures.get','models.available','media.status','media.models','media.catalog','media.timeline','projects.list','projects.get','assets.find','assets.hosts','assets.services','assets.graph','gamelab.setup','gamelab.hardware_check','gamelab.list_games','gamelab.list_projects','gamelab.get_project','setup.catalog','setup.history','setup.plan','setup.status','traffic.list','traffic.get','tests.list','tests.coverage','findings.list','dashboard.get','training.domains','training.workspaces','skills.list','skills.get'}
READ_TOOLS={'app_features','app_action','desktop_status','desktop_tools','desktop_call','desktop_describe','file_read','assets_query','install_plan','worker_status','delegate_task','plugin_list','plugin_tools','plugin_call','web_search','browser_workspace'}
READ_DESKTOP={'desktop_snapshot','desktop_find','desktop_get','desktop_is','desktop_screenshot','desktop_list_windows','desktop_list_displays','desktop_list_apps','desktop_list_surfaces','desktop_status','desktop_version','desktop_permissions','desktop_skills_list','desktop_skills_get','desktop_skills_path'}
READ_TOOLS.update({'library_sources','library_search','library_read'})
READ_TOOLS.update({'agent_skills','agent_skill_read','rea_status','rea_tools','rea_call','rea_evidence'})


def profile(store,provider,model):
 rows=store.setting('harness_model_profiles',{})
 return {'execution_mode':'auto','background':True,**rows.get(provider+':'+model,{})}


def save_profile(store,provider,model,execution_mode='auto',background=True):
 if execution_mode not in MODES:raise ValueError('Choose Plan, Auto or Full access.')
 if not isinstance(provider,str) or not isinstance(model,str) or len(provider+model)>300:raise ValueError('Invalid model profile.')
 store.require_desktop_owner()
 with store.connection(immediate=True) as db:
  import json
  row=db.execute("SELECT value FROM settings WHERE key='harness_model_profiles'").fetchone()
  rows=json.loads(row[0]) if row else {}
  rows[provider+':'+model]={'execution_mode':execution_mode,'background':bool(background)}
  db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('harness_model_profiles',?)",(json.dumps(rows),))
 return profile(store,provider,model)


def require_tool(session,name,args=None):
 if session.get('execution_mode')!='plan':return
 if name not in READ_TOOLS:raise PermissionError('Plan mode can inspect and propose steps; switch to Auto or Full access to make changes.')
 args=args or {}
 if name=='app_action' and args.get('method') not in READ_APP:raise PermissionError('This app action changes data. Plan mode only inspects.')
 if name=='desktop_call' and args.get('name') not in READ_DESKTOP:raise PermissionError('Plan mode cannot interact with desktop controls.')

DEFAULTS={'execution_mode':'auto','background':True,'web_search_mode':'auto','grants':['app','computer'],'permissions_configured':False,'scope':'all'}

def preferences(store,provider='',model='',chat_id=None):
 values={**DEFAULTS,**store.setting('harness_defaults',{}),**store.setting('harness_model_profiles',{}).get(provider+':'+model,{})}
 if chat_id:
  chat=store.get('chat',chat_id)
  if not chat:raise ValueError('Conversation not found.')
  values.update(chat.get('agent_settings',{}))
 return {**values,'chat_id':chat_id}

def save_preferences(store,provider='',model='',chat_id=None,project_id=None,scope='all',execution_mode='auto',background=True,web_search_mode='auto',grants=None):
 store.require_desktop_owner()
 if execution_mode not in MODES or scope not in {'all','model','chat'} or web_search_mode not in {'auto','on','off'}:raise ValueError('Choose valid agent settings.')
 if type(background) is not bool or not isinstance(grants,list) or set(grants)-{'app','computer','files','shell','install','integrations'} or not grants:raise ValueError('Choose the access this agent can use.')
 value={'execution_mode':execution_mode,'background':background,'web_search_mode':web_search_mode,'grants':sorted(set(grants)),'permissions_configured':True,'scope':scope}
 if scope=='all':
  store.set_setting('harness_defaults',value);store.set_setting('harness_model_profiles',{})
 elif scope=='model':
  if not provider or not model:raise ValueError('Choose a model first.')
  rows=store.setting('harness_model_profiles',{});rows[provider+':'+model]=value;store.set_setting('harness_model_profiles',rows)
 else:
  chat=store.get('chat',chat_id) if chat_id else None
  if chat_id and not chat:raise ValueError('Conversation not found.')
  if not chat:
   if project_id and not store.get('project',project_id):raise ValueError('Project not found.')
   chat=store.put('chat',{'title':'New chat','project_id':project_id,'messages':[]})
  store.put('chat',{**chat,'agent_settings':value},id=chat['id']);chat_id=chat['id']
 return preferences(store,provider,model,chat_id)


def should_search(message,mode='auto'):
 if mode=='off':return False
 if mode=='on':return True
 import re
 return bool(re.search(r'\b(?:search (?:the )?(?:web|internet|online)|look (?:it |this )?up|browse|latest|current (?:price|version|release|news)|today.s|find (?:online|sources)|check online|news|research online)\b',str(message),re.I))
