import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from bughunter.store import Store
from bughunter.desktop_bridge import DesktopBridge
from bughunter.operator_tools import OperatorTools
from bughunter.app import Application


def test_labeled_controller_uses_real_session_and_cleans_up_after_stop(tmp_path,monkeypatch):
 store=Store(tmp_path);bridge=DesktopBridge(store);calls=[]
 store.put('operator_run',{'status':'running'},id='oprun_fixture')
 async def mcp(operation,name=None,arguments=None,**kwargs):
  calls.append((name,arguments,kwargs))
  data={'session_id':'engine-session-fixture'} if name=='desktop_session_start' else {'rendered':True}
  return {'structuredContent':{'ok':True,'data':data}}
 monkeypatch.setattr(bridge,'_mcp',mcp)
 first=bridge.controller('oprun_fixture',{'model':'gpt-model-version','number':4,'label':'GPT model version #4'},'Calculator')
 assert first['engine_session_id']=='engine-session-fixture' and first['overlay_rendered']
 assert calls[1][1]['label']=='GPT model version #4 · Calculator'
 bridge.controller('oprun_fixture',{'model':'gpt-model-version','number':4,'label':'GPT model version #4'},'Calculator')
 assert len(calls)==2
 bridge.cancel_all();store.set_setting('emergency_stop',True)
 bridge.end_controller('oprun_fixture')
 assert [name for name,_,_ in calls[-2:]]==['desktop_cursor_overlay_disable','desktop_session_end']
 assert all(kwargs['cleanup'] for _,_,kwargs in calls[-2:])
 assert bridge.controller_record('oprun_fixture')['active'] is False
 assert store.get('operator_run','oprun_fixture')['status']=='running'


def test_model_cannot_disable_its_controller_indicator(tmp_path,monkeypatch):
 app=Application(tmp_path,primary=False)
 monkeypatch.setattr(app.operator,'_loop',lambda *args:None)
 task=app.harness.start('Control an app',['computer'])
 tools=OperatorTools(app,task['run_id'])
 with pytest.raises(PermissionError,match='automatically'):
  tools.desktop_call('desktop_cursor_overlay_disable',{})
 app.close()


def test_controller_serials_are_distinct_for_parallel_tasks(tmp_path,monkeypatch):
 app=Application(tmp_path,primary=False);monkeypatch.setattr(app.operator,'_loop',lambda *args:None)
 one=app.harness.start('Task one',['computer']);two=app.harness.start('Task two',['computer'])
 assert app.harness.controller_identity(one['id'])['number']!=app.harness.controller_identity(two['id'])['number']
 app.close()


def test_workflow_uses_existing_operator_adapter_and_stops_globally(tmp_path,monkeypatch):
 app=Application(tmp_path,primary=False);seen=[]
 monkeypatch.setattr(OperatorTools,'invoke_adapter',lambda self,name,**kwargs:seen.append((self.run_id,name)) or {'ok':True})
 task=app.workflows.start(spec={'nodes':[{'id':'one','adapter':'fixture'}]})
 app.workflows._running[task['id']].join(2)
 assert seen==[(task['id'],'fixture')]
 assert app.workflows.status(id=task['id'])['status']=='completed'
 app.store.set_setting('emergency_stop',True)
 with pytest.raises(ValueError,match='stopped'):app.workflows.start(spec={'nodes':[]})
 app.close()
