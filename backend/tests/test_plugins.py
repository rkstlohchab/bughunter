import json,sys,time
import pytest
from bughunter.app import Application
from bughunter import plugins

@pytest.fixture
def app(tmp_path,monkeypatch):
 secrets={}
 monkeypatch.setattr(plugins.keyring,'set_password',lambda service,name,value:secrets.__setitem__((service,name),value))
 monkeypatch.setattr(plugins.keyring,'get_password',lambda service,name:secrets.get((service,name)))
 monkeypatch.setattr(plugins.keyring,'delete_password',lambda service,name:secrets.pop((service,name),None))
 a=Application(tmp_path,primary=False)
 yield a,secrets
 a.close()

def test_custom_servers_are_extensible_and_secrets_never_enter_store(app):
 a,secrets=app;row=a.plugins.add('Future app',url='https://mcp.example.test/',auth='bearer',token='private-token',headers={'X-Token':'header-secret'})
 assert row['status']=='not_connected' and 'private-token' not in json.dumps(a.store.list('plugin'))
 assert 'header-secret' not in json.dumps(a.store.list('plugin')) and secrets
 assert {p['id'] for p in a.plugins.list()['presets']}=={'github','vercel'}
 for url in ('http://remote.example/mcp','https://user:secret@example.test','file:///private','https://example.test/?token=secret'):
  with pytest.raises(ValueError):a.plugins.add('Invalid',url=url)

def test_plan_blocks_unknown_or_mutating_plugin_tools_before_delivery(app,monkeypatch):
 a,_=app;row=a.plugins.add('Fixture',url='https://example.test');a.store.put('plugin',{**row,'status':'connected','tools':[{'name':'write','annotations':{}},{'name':'read','annotations':{'readOnlyHint':True}}]},id=row['id']);calls=[]
 async def request(*args,**kw):calls.append(args);return {'content':[{'type':'text','text':'read receipt'}]}
 monkeypatch.setattr(a.plugins,'_request',request)
 with pytest.raises(PermissionError):a.plugins.call(row['id'],'write',read_only=True)
 assert not calls
 assert a.plugins.call(row['id'],'read',read_only=True)['content'][0]['text']=='read receipt'
 a.plugins.dispatch('plugins.enable',{'id':row['id'],'enabled':False})
 with pytest.raises(PermissionError):a.plugins.call(row['id'],'read')

def test_real_stdio_mcp_discovery_and_call(app,tmp_path):
 a,_=app;server=tmp_path/'mcp_fixture.py';server.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("fixture")\n@m.tool(annotations={"readOnlyHint":True})\ndef sum_values(a:int,b:int)->int:\n return a+b\nm.run()\n')
 row=a.plugins.add('Local fixture',transport='stdio',command=sys.executable,args=[str(server)])
 a.plugins.connect(row['id']);deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  row=a.store.get('plugin',row['id'])
  if row['status'] not in {'connecting','awaiting_login'}:break
  time.sleep(.1)
 assert row['status']=='connected',row.get('error')
 assert a.plugins.tools(row['id'])['items'][0]['name']=='sum_values'
 result=a.plugins.call(row['id'],'sum_values',{'a':2,'b':3},read_only=True)
 assert '5' in json.dumps(result)
