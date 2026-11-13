"""Wire the installed agent-desktop MCP command catalog on macOS, Windows and Linux."""
from __future__ import annotations
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from . import process_runtime


class DesktopBridge:
    def __init__(self,store):
        self.store=store;self._catalog=None;self._stamp=0;self._stop=threading.Event();self._session_lock=threading.RLock()
        self._focus_thread=None;self._app_locks={}
        self._windows_cache=[];self._windows_at=0

    def executable(self):
        configured=self.store.setting('agent_desktop_path','')
        candidates=[configured,shutil.which('agent-desktop')]
        root=Path(__file__).resolve().parents[2]
        candidates += [str(root/'node_modules/.bin/agent-desktop'),'/opt/homebrew/bin/agent-desktop','/usr/local/bin/agent-desktop',str(Path.home()/'.npm-global/bin/agent-desktop')]
        if os.name=='nt':
            candidates += [str(Path(os.environ.get('APPDATA',''))/'npm/agent-desktop.cmd'),str(root/'node_modules/.bin/agent-desktop.cmd')]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():return str(Path(candidate).resolve())
        return None

    def check(self,check=None):
        if self._stop.is_set() or self.store.setting('emergency_stop',False):raise InterruptedError('Desktop work stopped.')
        if check:check()

    def cancel_all(self):self._stop.set()
    def reset(self):self._stop.clear()

    def controller_record(self,run_id):
        return self.store.get('desktop_session','desktop_'+run_id)

    def status(self):
        executable=self.executable()
        if not executable:return {'installed':False,'ready':False,'platform':__import__('platform').system(),'detail':'Install the desktop engine from Settings → Task harness.','tools':0}
        try:
            argv=[executable,'status']
            result=process_runtime.run(argv,timeout=12)
            payload=json.loads(result['stdout']);data=payload.get('data',{})
            access=data.get('permissions',{}).get('accessibility',{}).get('state')
            ready=bool(payload.get('ok')) and bool(data.get('supported_surfaces')) and access not in {'denied','not_granted','unknown'}
            error=payload.get('error') or {}
            return {'installed':True,'ready':ready,'executable':executable,'platform':data.get('platform'),'version':data.get('version'),
                    'permissions':data.get('permissions',{}),'supported_surfaces':data.get('supported_surfaces',[]),
                    'detail':'Desktop engine responds. App-specific actions still need observed controls.' if ready else (str(error.get('message')) if isinstance(error,dict) and error.get('message') else 'Open the engine permission guide or check your graphical desktop session.'),
                    'status_verified':ready,'tools':len(self._catalog or [])}
        except (ValueError,OSError,TimeoutError) as error:return {'installed':True,'ready':False,'detail':str(error),'tools':0}

    async def _mcp(self,operation,name=None,arguments=None,scope='status',check=None,cleanup=False):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client,StdioServerParameters
        executable=self.executable()
        if not executable:raise ValueError('Install agent-desktop from Task harness settings first.')
        saved=self.controller_record(scope) or {}
        session_id=saved.get('engine_session_id') if saved.get('active') else None
        args=['mcp','--session',session_id or 'bughunter-'+re.sub(r'[^A-Za-z0-9_-]','_',scope)[:48]]
        command,*args=process_runtime.cli_argv([executable,*args])
        # Desktop helpers do not need model/API credentials in their environment.
        env={key:value for key,value in os.environ.items() if not re.search(r'(API_KEY|ACCESS_TOKEN|AUTH_TOKEN|CLIENT_SECRET)$',key)}
        async def work():
            with tempfile.TemporaryFile(mode='w+') as errors:
                async with stdio_client(StdioServerParameters(command=command,args=args,env=env),errlog=errors) as streams:
                    async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=40)) as session:
                        await session.initialize()
                        if operation=='list':
                            tools=[];cursor=None
                            for _ in range(20):
                                result=await session.list_tools(cursor=cursor)
                                tools.extend({'name':tool.name,'description':tool.description or '', 'input_schema':tool.inputSchema} for tool in result.tools)
                                cursor=result.nextCursor
                                if not cursor:return tools
                            raise ValueError('Desktop tool catalog pagination did not finish.')
                        result=await session.call_tool(name,arguments or {})
                        return result.model_dump(mode='json',by_alias=True,exclude_none=True)
        task=asyncio.create_task(work())
        try:
            deadline=time.monotonic()+(8 if cleanup else 60)
            while not task.done():
                if not cleanup:self.check(check)
                if time.monotonic()>deadline:raise TimeoutError('Desktop call timed out. Observe the app before retrying an action.')
                await asyncio.sleep(.08)
            return await task
        finally:
            if not task.done():
                task.cancel()
                try:await task
                except BaseException:pass

    @staticmethod
    def payload(result):
        value=result.get('structuredContent')
        if value is None:
            for block in result.get('content',[]):
                if block.get('type')=='text':
                    try:value=json.loads(block.get('text',''));break
                    except ValueError:pass
        if not isinstance(value,dict):raise ValueError('Desktop engine did not return a structured receipt.')
        if value.get('ok') is not True:raise ValueError((value.get('error') or {}).get('message','Desktop engine failed.'))
        return value

    def windows(self):
        if time.monotonic()-self._windows_at<.5:return self._windows_cache
        result=process_runtime.run([self.executable(),'list-windows'],timeout=5)
        rows=json.loads(result['stdout']).get('data',[])
        if isinstance(rows,dict):rows=rows.get('windows',[])
        self._windows_cache=rows;self._windows_at=time.monotonic();return rows

    def target_name(self,arguments):
        if arguments.get('window_id'):
            try:return next((r['app_name'] for r in self.windows() if r['id']==arguments['window_id']),arguments.get('app',''))
            except Exception:pass
        target=str(arguments.get('app',''))
        return Path(target).stem if '/' in target or '\\' in target else target

    def focused(self,target,window_id=None):
        if not target or target=='Desktop':return False
        try:
            key=lambda value:re.sub(r'[^a-z0-9]','',str(value).casefold())
            return any(row.get('is_focused') and (row.get('id')==window_id if window_id else key(row.get('app_name',''))==key(target)) for row in self.windows())
        except Exception:return False

    @staticmethod
    def background_guard(name,arguments,background):
        if not background:return
        if name in {'desktop_press','desktop_mouse','desktop_hover','desktop_drag','desktop_focus','desktop_activate','desktop_hotkey'} or arguments.get('headed') or arguments.get('activate'):
            raise PermissionError('This step needs foreground input. Ask the user to switch this task to Foreground in the task panel; background work cannot take over their keyboard or mouse.')

    def _overlay(self,saved,visible,check=None):
        args={'label':saved['label'],'max_words':8,'accent':saved['accent'],'size':.65,'dwell_ms':120} if visible else {}
        command='desktop_cursor_overlay_enable' if visible else 'desktop_cursor_overlay_disable'
        result=asyncio.run(self._mcp('call',command,args,scope=saved['run_id'],check=check))
        rendered=bool(self.payload(result).get('data',{}).get('rendered')) if visible else False
        saved={**saved,'overlay_rendered':rendered,'overlay_visible':visible and rendered,'overlay_checked':True}
        self.store.put('desktop_session',saved,id=saved['id']);return saved

    def _watch_focus(self):
        while not self._stop.wait(.7):
            rows=[row for row in self.store.list('desktop_session') if row.get('active') and row.get('background')]
            if not rows:return
            for row in rows:
                try:
                    visible=self.focused(row.get('target',''),row.get('window_id'))
                    with self._session_lock:
                        latest=self.controller_record(row['run_id']) or {}
                        if latest.get('active') and (visible!=latest.get('overlay_visible',False) or not latest.get('overlay_checked')):self._overlay(latest,visible)
                except Exception:pass

    def controller(self,run_id,identity,target='',check=None,background=False,window_id=None):
        """A real engine session is needed for labeled cursors, not just a ref namespace."""
        self.check(check)
        with self._session_lock:
            saved=self.controller_record(run_id) or {}
            target=str(target or saved.get('target') or 'Desktop')[:60]
            if window_id is None and target==saved.get('target'):window_id=saved.get('window_id')
            label=(str(identity['label'])+' · '+target)[:160]
            if not saved.get('active'):
                result=asyncio.run(self._mcp('call','desktop_session_start',{'name':label,'no_trace':True},scope=run_id,check=check))
                engine_id=self.payload(result)['data']['session_id']
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',engine_id):raise ValueError('Invalid engine session identity.')
                saved={'id':'desktop_'+run_id,'run_id':run_id,'engine_session_id':engine_id,'active':True}
                self.store.put('desktop_session',saved,id=saved['id'])
            if saved.get('label')!=label or saved.get('window_id')!=window_id:
                accents=['#69BFE8','#B89BF5','#7FD3AA','#F2BB77','#EF98BC','#86B8FF']
                accent=accents[(int(identity['number'])-1)%len(accents)]
                try:
                    saved={**saved,**identity,'target':target,'label':label,'accent':accent,'background':background,'window_id':window_id}
                    saved=self._overlay(saved,not background or self.focused(target,window_id),check)
                    rendered=saved.get('overlay_rendered',False)
                    error=None
                except InterruptedError:raise
                except Exception as exc:rendered=False;error=str(exc)[:250]
                saved={**saved,**identity,'target':target,'label':label,'overlay_rendered':rendered,'overlay_error':error,'updated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
                self.store.put('desktop_session',saved,id=saved['id'])
            if background and (not self._focus_thread or not self._focus_thread.is_alive()):
                self._focus_thread=threading.Thread(target=self._watch_focus,daemon=True,name='controller-focus');self._focus_thread.start()
            return saved

    def end_controller(self,run_id):
        with self._session_lock:
            saved=self.controller_record(run_id)
            if not saved or not saved.get('active'):return
            errors=[];closed=False
            for command in ('desktop_cursor_overlay_disable','desktop_session_end'):
                try:self.payload(asyncio.run(self._mcp('call',command,{},scope=run_id,cleanup=True)));closed=True
                except Exception as exc:errors.append(str(exc)[:180])
            self.store.put('desktop_session',{**saved,'active':not closed,'overlay_rendered':saved.get('overlay_rendered',False) if not closed else False,'cleanup_errors':errors},id=saved['id'])

    def end_all(self):
        for saved in self.store.list('desktop_session'):
            if saved.get('active'):self.end_controller(saved['run_id'])
        if any(saved.get('active') for saved in self.store.list('desktop_session')):raise ValueError('Some controller indicator sessions could not be closed. Check desktop engine status.')

    def tools(self,refresh=False):
        if self._catalog is None or refresh or time.monotonic()-self._stamp>300:
            self._catalog=asyncio.run(self._mcp('list'));self._stamp=time.monotonic()
        return self._catalog

    def call(self,name,arguments,run_id,check=None,background=False):
        self.check(check)
        self.background_guard(name,arguments,background)
        arguments=dict(arguments)
        if background and name=='desktop_launch':arguments['activate']=False
        if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):raise ValueError('Invalid desktop task ID.')
        if not isinstance(arguments,dict) or len(json.dumps(arguments))>32768:raise ValueError('Desktop arguments must be a bounded JSON object.')
        if name not in {tool['name'] for tool in self.tools()}:raise ValueError('Choose an exact tool from desktop_tools.')
        # Semantic actions on a shared app are serialized; unrelated app windows can proceed.
        app_key=str(arguments.get('app') or 'desktop').casefold()
        with self._session_lock:lock=self._app_locks.setdefault(app_key,threading.Lock())
        while not lock.acquire(timeout=.1):self.check(check)
        try:result=asyncio.run(self._mcp('call',name,arguments,scope=run_id,check=check))
        finally:lock.release()
        # Images stay as private artifacts; no base64 desktop data in text logs.
        content=[]
        for index,block in enumerate(result.get('content',[])):
            if block.get('type')=='image':
                import base64
                folder=self.store.root/'artifacts'/'desktop'/run_id;folder.mkdir(parents=True,exist_ok=True)
                raw=base64.b64decode(block.get('data',''),validate=True)
                if len(raw)>12*1024**2:raise ValueError('Desktop screenshot exceeds the artifact limit.')
                path=folder/f'{time.time_ns()}-{index}.png';path.write_bytes(raw);path.chmod(0o600)
                content.append({'type':'image_artifact','path':str(path),'mimeType':block.get('mimeType')})
            elif block.get('type')=='text':
                value=str(block.get('text',''))
                try:duplicate=json.loads(value)==result.get('structuredContent')
                except ValueError:duplicate=False
                if not duplicate:content.append({'type':'text','text':value[:100000]})
        result['content']=content
        return result
