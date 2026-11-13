"""Google-account Gemini CLI over ACP, with no CLI tools or filesystem capability."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from .security import redact_text

MODEL_OPTIONS=[{'id':'gemini-3.8-flash','label':'Gemini 3.8 Flash'},
               {'id':'gemini-3.7-flash','label':'Gemini 3.7 Flash'},
               {'id':'gemini-3.1-pro-preview','label':'Gemini 3.1 Pro (preview)'}]
_ACTIVE=set()
_LOCK=threading.Lock()


def connection_status():
    installed=bool(shutil.which('gemini'))
    signed_in=(Path.home()/'.gemini/oauth_creds.json').is_file()
    return {'ready':installed and signed_in,'available':installed,'models':MODEL_OPTIONS,
            'detail':('Cached Google login found; model access is checked when used.' if installed and signed_in else
                      'Open Gemini CLI and sign in with the Google account for your AI Pro plan, then refresh.' if installed else
                      'Install Gemini CLI, sign in with your Google AI Pro account, then refresh.'),
            'source':'Google documented versions; subscription access varies'}


class GeminiSession:
    def __init__(self,model,effort='',cancel_check=None,timeout=240):
        self.model=model;self.effort=effort;self.cancel_check=cancel_check;self.timeout=timeout
        self.events=queue.Queue();self.sequence=0;self.process=None;self.workspace=None
        self.session_id=None;self.text=[];self._closed=False

    def check(self):
        if self._closed:raise InterruptedError('Gemini request cancelled.')
        if self.cancel_check:self.cancel_check()

    def __enter__(self):
        self.check()
        if not connection_status()['ready']:raise ValueError(connection_status()['detail'])
        if self.model not in {model['id'] for model in MODEL_OPTIONS}:raise ValueError('Choose a supported Gemini version.')
        if self.effort and self.effort not in {'low','medium','high'}:raise ValueError('Gemini supports low, medium or high effort here.')
        self.workspace=tempfile.TemporaryDirectory(prefix='bh-gemini-')
        work=Path(self.workspace.name)
        config={'tools':{'core':[]},'mcp':{'allowed':[str(uuid.uuid4())]},'mcpServers':{},
                'context':{'fileName':str(uuid.uuid4())+'.md','discoveryMaxDirs':0},
                'extensions':{'disabled':['*']},'hooks':{'enabled':False},'telemetry':{'enabled':False},
                'security':{'auth':{'selectedType':'oauth-personal'}},'model':{'name':self.model}}
        if self.effort:
            config['modelConfigs']={'customAliases':{self.model:{'modelConfig':{'model':self.model,
                'generateContentConfig':{'thinkingConfig':{'thinkingLevel':self.effort.upper()}}}}}}
        settings=work/'settings.json';settings.write_text(json.dumps(config));settings.chmod(0o600)
        env={key:value for key,value in os.environ.items() if key not in
             {'GEMINI_API_KEY','GOOGLE_API_KEY','GOOGLE_APPLICATION_CREDENTIALS','GOOGLE_GENAI_USE_VERTEXAI'}}
        env.update(GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(settings),NO_BROWSER='true',GEMINI_TELEMETRY_ENABLED='false')
        try:
            # 0.20 uses --experimental-acp; newer versions expose --acp.
            from .process_runtime import run
            help_result=run([shutil.which('gemini'),'--help'],timeout=15,env=env,check=self.check)
            flag='--acp' if '--acp' in help_result['stdout'] and '--experimental-acp' not in help_result['stdout'] else '--experimental-acp'
            from .process_runtime import cli_argv
            args=cli_argv([shutil.which('gemini'),flag,'--model',self.model,
                '--allowed-mcp-server-names',config['mcp']['allowed'][0],'--extensions',''])
            self.process=subprocess.Popen(args,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,
                cwd=work,env=env,**({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}))
            self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
            with _LOCK:_ACTIVE.add(self)
            self.call('initialize',{'protocolVersion':1,'clientCapabilities':{'fs':{'readTextFile':False,'writeTextFile':False}}})
            self.session_id=self.call('session/new',{'cwd':str(work),'mcpServers':[]})['sessionId']
            return self
        except BaseException:
            self.close();raise

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line)>2_000_000:self.events.put({'fatal':'Gemini response exceeded the size limit.'});break
                try:self.events.put(json.loads(line))
                except ValueError:continue
        finally:self.events.put({'fatal':'Gemini CLI exited. Check sign-in, model access and CLI version.'})

    def send(self,message):
        self.check();self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()

    def call(self,method,params):
        self.sequence+=1;ident=self.sequence
        self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            self.check()
            try:event=self.events.get(timeout=.1)
            except queue.Empty:continue
            if event.get('fatal'):raise ValueError(event['fatal'])
            if event.get('id')==ident:
                if event.get('error'):raise ValueError(redact_text(str(event['error'].get('message','Gemini request failed.'))))
                return event.get('result',{})
            if 'id' in event and event.get('method'):
                if event['method']=='session/request_permission':
                    self.send({'jsonrpc':'2.0','id':event['id'],'result':{'outcome':{'outcome':'cancelled'}}})
                else:self.send({'jsonrpc':'2.0','id':event['id'],'error':{'code':-32601,'message':'No client capabilities are exposed.'}})
            update=event.get('params',{}).get('update',{})
            if update.get('sessionUpdate')=='tool_call':raise ValueError('Gemini requested an unavailable CLI tool.')
            if update.get('sessionUpdate')=='agent_message_chunk' and update.get('content',{}).get('type')=='text':
                self.text.append(update['content']['text'])
                if sum(map(len,self.text))>100_000:raise ValueError('Gemini response exceeded the text limit.')
        raise TimeoutError('Gemini timed out. Completed course checkpoints are preserved.')

    def run(self,system,prompt,images=()):
        self.text=[]
        content=[{'type':'text','text':system+'\n\n'+prompt}]
        if len(images)>3:raise ValueError('Choose at most three sampled images.')
        for image in images:
            path=Path(image)
            if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=10*1024**2:raise ValueError('Invalid sampled image.')
            raw=path.read_bytes();mime='image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/png' if raw.startswith(b'\x89PNG') else None
            if not mime:raise ValueError('Use JPEG or PNG sampled images.')
            content.append({'type':'image','mimeType':mime,'data':base64.b64encode(raw).decode()})
        result=self.call('session/prompt',{'sessionId':self.session_id,'prompt':content})
        if result.get('stopReason')!='end_turn':raise ValueError('Gemini did not complete this request.')
        text=''.join(self.text).strip()
        if not text:raise ValueError('Gemini returned no readable answer.')
        return redact_text(text)

    def close(self):
        self._closed=True
        with _LOCK:_ACTIVE.discard(self)
        if self.process:
            if self.process.poll() is None:
                from .process_runtime import terminate
                terminate(self.process)
            for stream in (self.process.stdin,self.process.stdout):
                if stream:
                    try:stream.close()
                    except OSError:pass
        if self.workspace:self.workspace.cleanup()

    def __exit__(self,*_):self.close()


def cancel_all():
    with _LOCK:sessions=list(_ACTIVE)
    for session in sessions:session.close()


def complete(messages,system,model,effort='',cancel_check=None,images=()):
    prompt='\n\n'.join(message['role']+': '+str(message.get('content','')) for message in messages if message['role']!='system')
    with GeminiSession(model,effort,cancel_check) as session:return session.run(system,prompt,images)
