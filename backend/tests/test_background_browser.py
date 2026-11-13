from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading,time
import pytest
from bughunter.app import Application
from bughunter.operator_tools import OperatorTools

@pytest.fixture
def app(tmp_path,monkeypatch):
 a=Application(tmp_path,primary=False);monkeypatch.setattr(a.operator,'_loop',lambda *args:None)
 yield a
 a.close()

@pytest.fixture
def url():
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<title>Background fixture</title><label>Name<input aria-label="Name"></label><button onclick="document.getElementById(\'result\').innerText=\'Saved \'+document.querySelector(\'input\').value">Save</button><p id="result">Waiting</p>')
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 yield f'http://127.0.0.1:{server.server_port}'
 server.shutdown()

def test_real_background_browser_reads_fills_and_clicks_without_os_input(app,url):
 task=app.harness.start('Use a separate browser',['computer']);tools=OperatorTools(app,task['run_id'])
 page=tools.browser_workspace('open',url=url);assert page['background'] and page['title']=='Background fixture'
 name=next(control['ref'] for control in page['controls'] if control['name']=='Name')
 page=tools.browser_workspace('fill',ref=name,text='Ada')
 with pytest.raises(ValueError,match='stale'):tools.browser_workspace('click',ref=name)
 button=next(control['ref'] for control in page['controls'] if control['name']=='Save')
 page=tools.browser_workspace('click',ref=button);assert 'Saved Ada' in page['text']
 view=app.harness.dispatch('harness.browser.view',{'id':task['id']});assert view['image_url'].startswith('data:image/jpeg') and view['cursor']['label']
 app.stop_all();assert app.browser_workspaces.view(task['id'])['state']=='closed'

def test_plan_allows_page_observation_but_blocks_writes(app,url):
 task=app.harness.start('Inspect a page',execution_mode='plan');tools=OperatorTools(app,task['run_id']);page=tools.browser_workspace('open',url=url)
 with pytest.raises(PermissionError,match='Plan mode'):tools.browser_workspace('click',ref=page['controls'][0]['ref'])

def test_live_host_capabilities_include_real_file_writer_and_background_browser(app,monkeypatch):
 from bughunter.operator_agent import OperatorAgent
 task=app.harness.start('Create a notebook',execution_mode='full');seen=[]
 def turn(**kw):seen.extend(kw['transcript']);return {'message':{'role':'assistant','content':'Reply'},'tool_calls':[]}
 monkeypatch.setattr('bughunter.provider_api.single_tool_turn',turn)
 OperatorAgent._loop(app.operator,task['run_id'],threading.Event())
 state=next(m['content'] for m in seen if m.get('host_capabilities'))
 assert 'file_write' in state and 'browser_workspace' in state and 'does not restrict' in state
 app.harness.stop_all();assert app.harness.get(task['id'])['status']=='awaiting_user'

def test_missing_browser_gives_installer_action(app,monkeypatch):
 from bughunter.tool_installer import ToolInstaller
 plan=app.tool_installer.plan('browser-runtime')
 assert '--only-shell' in plan['argv'] and plan['installed']
