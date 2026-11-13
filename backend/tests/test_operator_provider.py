import json
import pytest
from bughunter.provider_api import single_tool_turn

SPECS=[{'name':'app_action','description':'Invoke an app action','input_schema':{'type':'object','properties':{'method':{'type':'string'}},'required':['method']}}]
MESSAGES=[{'role':'system','content':'Host instructions'},{'role':'user','content':'Find the installed app'}]


def test_codex_selected_model_and_effort_can_request_real_host_tool(monkeypatch):
 seen={}
 class Session:
  def __init__(self,**kw):seen['check']=kw['cancel_check']
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def run(self,model,system,prompt,**kw):
   seen.update(model=model,system=system,effort=kw['reasoning_effort'])
   return '{"tool":"app_action","arguments":{"method":"system.apps"}}'
 monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
 response=single_tool_turn(provider_cfg={'provider':'codex','model':'chosen-version','reasoning_effort':'medium'},transcript=MESSAGES,tool_specs=SPECS,check=lambda:None)
 assert seen['model']=='chosen-version' and seen['effort']=='medium'
 assert 'required' in seen['system']
 assert response['tool_calls'][0]['name']=='app_action'
 assert response['message']['tool_calls'][0]['id']==response['tool_calls'][0]['id']


def test_claude_selected_version_uses_isolated_cli_and_cancellation(monkeypatch):
 seen={}
 monkeypatch.setattr('shutil.which',lambda name:'/fixture/'+name)
 def run(argv,**kw):
  seen.update(argv=argv,**kw);return {'exit_code':0,'stdout':'Which detected device should I use?','stderr':''}
 monkeypatch.setattr('bughunter.process_runtime.run',run)
 check=lambda:None
 result=single_tool_turn(provider_cfg={'provider':'claude_code','model':'claude-sonnet-fixture','reasoning_effort':'high'},transcript=MESSAGES,tool_specs=SPECS,check=check)
 assert result['message']['content'].startswith('Which') and not result['tool_calls']
 assert seen['check'] is check and '--strict-mcp-config' in seen['argv'] and '--effort' in seen['argv']
 assert seen['argv'][seen['argv'].index('--model')+1]=='claude-sonnet-fixture'


def test_provider_cannot_silently_bypass_local_only_or_pricing():
 with pytest.raises(ValueError,match='Local-only'):
  single_tool_turn(provider_cfg={'provider':'codex','model':'x','local_only':True},transcript=MESSAGES,tool_specs=SPECS)
 with pytest.raises(ValueError,match='pricing'):
  single_tool_turn(provider_cfg={'provider':'openai','model':'x'},transcript=MESSAGES,tool_specs=SPECS)


def test_fallback_keeps_tool_call_ids_and_arguments(monkeypatch):
 calls=[]
 def request(url,payload,*args,**kwargs):
  calls.append(payload)
  if len(calls)==1:raise RuntimeError('tools not supported')
  return {'choices':[{'message':{'content':'{"tool":"app_action","arguments":{"method":"system.apps"}}'}}]}
 monkeypatch.setattr('bughunter.provider_api._request_json',request)
 result=single_tool_turn(provider_cfg={'provider':'ollama','model':'fixture'},transcript=MESSAGES,tool_specs=SPECS)
 assert result['tool_calls'][0]['input']=={'method':'system.apps'}
 assert result['message']['tool_calls'][0]['id']==result['tool_calls'][0]['id']


def test_plain_subscription_completion_does_not_advertise_unavailable_tools(monkeypatch):
 from bughunter.provider_api import simple_completion
 class Session:
  def __init__(self,**kwargs):pass
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def run(self,model,system,prompt,**kwargs):
   assert 'probe_url' not in system and 'Tool protocol' not in system
   return '{"question":"Grounded?","answer":"Yes"}'
 monkeypatch.setattr('bughunter.codex_subscription.CodexSession',Session)
 assert 'Grounded?' in simple_completion({'provider':'codex','model':'fixture'},'Generate a pair')
