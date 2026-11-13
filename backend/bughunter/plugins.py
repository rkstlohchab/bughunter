"""User-configured MCP integrations, shared by every model through host tools."""
from __future__ import annotations
import asyncio,contextlib,json,os,queue,re,secrets,threading,time,webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import keyring
from .security import redact
from .store import new_id
from . import process_runtime

PRESETS=[{'id':'github','name':'GitHub','url':'https://api.githubcopilot.com/mcp/','auth':'bearer','detail':'Repositories, issues and pull requests. Use a GitHub personal access token with the scopes you need.','docs':'https://github.com/github/github-mcp-server'},
 {'id':'vercel','name':'Vercel','url':'https://mcp.vercel.com','auth':'oauth','detail':'Projects, deployment inspection, documentation and supported platform actions. Connect your Vercel account.','docs':'https://vercel.com/docs/agent-resources/vercel-mcp'}]
SERVICE='BugHunterClaudex.plugins'

class SecretStorage:
 def __init__(self,id):self.id=id
 async def get_tokens(self):
  from mcp.shared.auth import OAuthToken
  value=keyring.get_password(SERVICE,self.id+':oauth_tokens');return OAuthToken.model_validate_json(value) if value else None
 async def set_tokens(self,tokens):keyring.set_password(SERVICE,self.id+':oauth_tokens',tokens.model_dump_json())
 async def get_client_info(self):
  from mcp.shared.auth import OAuthClientInformationFull
  value=keyring.get_password(SERVICE,self.id+':oauth_client');return OAuthClientInformationFull.model_validate_json(value) if value else None
 async def set_client_info(self,info):keyring.set_password(SERVICE,self.id+':oauth_client',info.model_dump_json())

class PluginService:
 def __init__(self,store):self.store=store;self._cancel={};self._jobs={};self._lock=threading.RLock()
 def list(self):return {'presets':PRESETS,'items':self.store.list('plugin'),'transports':['http','stdio']}
 def add(self,name,url='',transport='http',command='',args=None,auth='none',token='',preset=None,headers=None,env=None):
  self.store.require_desktop_owner()
  if not isinstance(name,str) or not 1<=len(name.strip())<=70:raise ValueError('Enter a plugin name up to 70 characters.')
  if transport not in {'http','stdio'} or auth not in {'none','bearer','oauth'}:raise ValueError('Choose a supported transport and authentication method.')
  if transport=='http':
   parsed=urlsplit(url)
   if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'}):raise ValueError('Use HTTPS, or HTTP on localhost for your local MCP server.')
   if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:raise ValueError('Use an MCP endpoint without URL credentials or a fragment.')
  if transport=='stdio' and (not isinstance(command,str) or not command or len(command)>500 or not isinstance(args or [],list) or any(not isinstance(a,str) or len(a)>2000 for a in args or [])):raise ValueError('Enter an executable and a JSON array of separate arguments.')
  for values in (headers or {},env or {}):
   if not isinstance(values,dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>8000 or '\n' in k or '\r' in k for k,v in values.items()):raise ValueError('Headers and environment must be bounded string maps.')
  if env and any(k.upper() in {'HOME','USERPROFILE','CODEX_HOME','PATH','PYTHONPATH','LD_PRELOAD','DYLD_INSERT_LIBRARIES'} for k in env):raise ValueError('Keep runtime paths and loader settings outside plugin environment overrides.')
  id=new_id('plugin')
  if token:keyring.set_password(SERVICE,id+':bearer',token)
  if headers:keyring.set_password(SERVICE,id+':headers',json.dumps(headers))
  if env:keyring.set_password(SERVICE,id+':env',json.dumps(env))
  return self.store.put('plugin',{'name':name.strip(),'url':url,'transport':transport,'command':command,'args':args or [],'auth':auth,'preset':preset,'enabled':True,'status':'not_connected','tools':[],'header_names':list(headers or {}),'env_names':list(env or {})},id=id)
 def _row(self,id):
  row=self.store.get('plugin',id)
  if not row:raise ValueError('Plugin not found.')
  if not row.get('enabled'):raise PermissionError('This plugin is disabled. Enable it in Settings first.')
  return row
 def _check(self,id,generation,check=None,event=None):
  if self.store.setting('emergency_stop',False) or self.store.setting('stop_generation',0)!=generation or (event or self._cancel.get(id,threading.Event())).is_set():raise InterruptedError('Plugin work stopped.')
  self._row(id)
  if check:check()
 async def _request(self,row,operation,name=None,arguments=None,interactive=False,check=None):
  from mcp import ClientSession
  from mcp.client.stdio import stdio_client,StdioServerParameters
  from mcp.client.streamable_http import streamablehttp_client
  generation=self.store.setting('stop_generation',0);id=row['id'];server=None
  with self._lock:
   event=self._cancel.get(id)
   if event is None or event.is_set():event=threading.Event();self._cancel[id]=event
  async def work():
   nonlocal server
   async with contextlib.AsyncExitStack() as stack:
    if row['transport']=='stdio':
     env={k:v for k,v in os.environ.items() if not re.search(r'(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL)',k,re.I)}
     env.update(json.loads(keyring.get_password(SERVICE,id+':env') or '{}'))
     executable,*args=process_runtime.cli_argv([row['command'],*row['args']])
     import tempfile
     errors=stack.enter_context(tempfile.TemporaryFile(mode='w+'))
     streams=await stack.enter_async_context(stdio_client(StdioServerParameters(command=executable,args=args,env=env),errlog=errors))
    else:
     headers=json.loads(keyring.get_password(SERVICE,id+':headers') or '{}');auth=None
     if row['auth']=='bearer':
      token=keyring.get_password(SERVICE,id+':bearer')
      if not token:raise ValueError('Save an access token in the plugin connection first.')
      headers['Authorization']='Bearer '+token
     if row['auth']=='oauth':
      from mcp.client.auth import OAuthClientProvider
      from mcp.shared.auth import OAuthClientMetadata
      incoming=queue.Queue();nonce=secrets.token_urlsafe(20)
      class Callback(BaseHTTPRequestHandler):
       def log_message(self,*args):pass
       def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path!='/callback/'+nonce:self.send_error(404);return
        query=parse_qs(parsed.query)
        if not query.get('code') or not query.get('state'):self.send_error(400);return
        incoming.put((query['code'][0][:8000],query['state'][0][:8000]));self.send_response(200);self.end_headers();self.wfile.write(b'Connection received. Return to BugHunterClaudex.')
      server=ThreadingHTTPServer(('127.0.0.1',row.get('oauth_port',0)),Callback);
      if not row.get('oauth_port'):self.store.put('plugin',{**self._row(id),'oauth_port':server.server_port},id=id)
      server.daemon_threads=True
      threading.Thread(target=server.serve_forever,daemon=True).start();redirect=f'http://127.0.0.1:{server.server_port}/callback/{nonce}'
      async def browser(url):
       if not interactive:raise PermissionError('This connection needs sign-in. Reconnect it from Settings → Plugins.')
       if urlsplit(url).scheme!='https':raise ValueError('The plugin returned a non-HTTPS sign-in page.')
       self.store.put('plugin',{**self._row(id),'status':'awaiting_login','auth_url':url},id=id);webbrowser.open(url)
      async def callback():
       deadline=time.monotonic()+300
       while time.monotonic()<deadline:
        self._check(id,generation,check,event)
        try:return incoming.get_nowait()
        except queue.Empty:await asyncio.sleep(.1)
       raise TimeoutError('Sign-in timed out. Reconnect and complete the browser sign-in.')
      auth=OAuthClientProvider(row['url'],OAuthClientMetadata(client_name='BugHunterClaudex',redirect_uris=[redirect],grant_types=['authorization_code','refresh_token'],response_types=['code'],token_endpoint_auth_method='none'),SecretStorage(id),browser,callback)
     streams=await stack.enter_async_context(streamablehttp_client(row['url'],headers=headers,auth=auth,timeout=30,sse_read_timeout=90))
    session=await stack.enter_async_context(ClientSession(streams[0],streams[1],read_timeout_seconds=timedelta(seconds=90)))
    await session.initialize()
    if operation=='list':
     tools=[];cursor=None
     for _ in range(50):
      result=await session.list_tools(cursor=cursor);tools.extend({'name':t.name,'description':t.description or '', 'input_schema':t.inputSchema,'annotations':t.annotations.model_dump(exclude_none=True) if t.annotations else {}} for t in result.tools);cursor=result.nextCursor
      if not cursor:return tools
     raise ValueError('Plugin catalog did not finish pagination. Narrow its server toolsets.')
    result=await session.call_tool(name,arguments or {});return result.model_dump(mode='json',by_alias=True,exclude_none=True)
  task=asyncio.create_task(work())
  try:
   deadline=time.monotonic()+(360 if interactive else 120)
   while not task.done():
    self._check(id,generation,check,event)
    if time.monotonic()>deadline:raise TimeoutError('Plugin call timed out; inspect its task receipt before retrying changes.')
    await asyncio.sleep(.1)
   return await task
  finally:
   if not task.done():task.cancel()
   await asyncio.gather(task,return_exceptions=True)
   if server:server.shutdown();server.server_close()
 def connect(self,id):
  self.store.require_desktop_owner();row=self._row(id)
  with self._lock:
   if self._jobs.get(id) and self._jobs[id].is_alive():raise ValueError('This plugin is already connecting.')
   self._cancel[id]=threading.Event();self.store.put('plugin',{**row,'status':'connecting','error':None},id=id)
   def run():
    try:
     tools=asyncio.run(self._request(row,'list',interactive=True));self.store.put('plugin',{**self._row(id),'tools':tools,'status':'connected','error':None,'auth_url':None},id=id)
    except Exception as error:
     latest=self.store.get('plugin',id)
     if latest:self.store.put('plugin',{**latest,'status':'not_connected','error':str(error)[:500],'auth_url':None},id=id)
   thread=threading.Thread(target=run,daemon=True,name='plugin:'+id);self._jobs[id]=thread;thread.start()
  return self.store.get('plugin',id)
 def tools(self,id,query=''):
  row=self._row(id)
  return {'plugin_id':id,'name':row['name'],'status':row['status'],'items':[t for t in row['tools'] if not query or query.lower() in (t['name']+' '+t['description']).lower()][:30],'more':max(0,len(row['tools'])-30),'next':'Search by exact tool name to see its full argument schema.'}
 def call(self,id,name,arguments=None,check=None,read_only=False):
  row=self._row(id)
  if row.get('status')!='connected':raise ValueError('Connect this plugin in Settings before using it.')
  tool=next((t for t in row['tools'] if t['name']==name),None)
  if not tool:raise ValueError('Discover an exact plugin tool before calling it.')
  annotations=tool.get('annotations') or {}
  if read_only and (annotations.get('readOnlyHint') is not True or annotations.get('destructiveHint') is True):raise PermissionError('Plan and ordinary chat permit verified read tools only. Start a task with Connected integrations access to make changes.')
  if not isinstance(arguments or {},dict) or len(json.dumps(arguments or {}))>128000:raise ValueError('Plugin arguments must be a bounded JSON object.')
  result=asyncio.run(self._request(row,'call',name,arguments,check=check));return redact(result)
 def cancel_all(self):
  for event in self._cancel.values():event.set()
 def dispatch(self,method,p):
  if method=='plugins.list':return self.list()
  if method=='plugins.add':return self.add(**p)
  if method=='plugins.connect':return self.connect(p['id'])
  if method=='plugins.tools':return self.tools(**p)
  if method=='plugins.enable':
   row=self.store.get('plugin',p['id'])
   if not row:raise ValueError('Plugin not found.')
   if not p.get('enabled'):self._cancel.setdefault(p['id'],threading.Event()).set()
   else:self._cancel[p['id']]=threading.Event()
   return self.store.put('plugin',{**row,'enabled':bool(p['enabled'])},id=row['id'])
  if method=='plugins.remove':
   self._cancel.setdefault(p['id'],threading.Event()).set()
   for key in ('bearer','headers','env','oauth_tokens','oauth_client'):
    try:keyring.delete_password(SERVICE,p['id']+':'+key)
    except keyring.errors.PasswordDeleteError:pass
   return {'deleted':self.store.delete('plugin',p['id'])}
  raise ValueError('Unknown plugin action.')
