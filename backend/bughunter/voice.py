"""Local streaming speech, managed model setup and ephemeral PCM sessions."""
from __future__ import annotations
import base64,json,os,platform,queue,re,shutil,subprocess,sys,threading,time
from pathlib import Path
from . import process_runtime

MODELS={
 'tiny':{'label':'Moonshine Tiny Streaming · English','arch':2,'detail':'Lowest memory and latency. Start here on an 8 GB Mac.','download_estimate_mb':130},
 'small':{'label':'Moonshine Small Streaming · English','arch':4,'detail':'Larger English model; compare accuracy on your own speech.','download_estimate_mb':350},
 'medium':{'label':'Moonshine Medium Streaming · English','arch':5,'detail':'Recommended for accuracy. 245M parameters, local streaming; uses more memory than Small.','download_estimate_mb':600},
}
WORKER=r'''
import sys,json,base64,array,time
from pathlib import Path
from moonshine_voice import Transcriber,ModelArch,get_model_for_language
from moonshine_voice.transcriber import TranscriptEventListener

def emit(**data):print(json.dumps(data),flush=True)
try:
 cfg=json.loads(sys.argv[1]);arch=ModelArch(cfg['arch'])
 if cfg['action']=='setup':
  path,arch=get_model_for_language('en',arch,cache_root=Path(cfg['cache']),on_progress=lambda fraction,file:emit(kind='progress',fraction=fraction,file=str(file)))
 else:path=cfg['path']
 start=time.monotonic();t=Transcriber(path,arch,update_interval=.3)
 if cfg['action']=='setup':
  t.start();t.add_audio([0.0]*16000,16000);t.stop();emit(kind='verified',path=str(path),load_ms=round((time.monotonic()-start)*1000));sys.exit(0)
 class Listener(TranscriptEventListener):
  def on_line_text_changed(self,event):emit(kind='partial',text=event.line.text,line_id=str(event.line.line_id))
  def on_line_completed(self,event):emit(kind='final',text=event.line.text,line_id=str(event.line.line_id))
 t.add_listener(Listener());t.start();emit(kind='ready')
 for line in sys.stdin:
  cmd=json.loads(line)
  if cmd.get('stop'):break
  samples=array.array('h');samples.frombytes(base64.b64decode(cmd['pcm']))
  if sys.byteorder!='little':samples.byteswap()
  t.add_audio([x/32768.0 for x in samples],16000)
 t.stop();emit(kind='stopped')
except Exception as error:emit(kind='error',error=str(error));sys.exit(1)
'''


def clean_speech(text):
 return re.sub(r'\s+',' ',re.sub(r'\b(?:uh+|um+|uhm+|erm+)\b[, ]*','',str(text),flags=re.I)).strip()


class VoiceService:
 def __init__(self,store):
  self.store=store;self.root=store.root/'runtimes';self.env=self.root/'voice';self._lock=threading.RLock();self._sessions={};self._cancel=threading.Event();self._setup=False
  self.root.mkdir(exist_ok=True)
 def python(self):return self.env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
 def state(self):return self.store.setting('voice_runtime',{})
 def status(self):
  state=self.state();models=state.get('models',{})
  return {**state,'selected':state.get('selected','tiny'),'models':[{'id':id,**spec,**models.get(id,{})} for id,spec in MODELS.items()],
   'local':True,'recording':bool(self._sessions),'setup_running':self._setup,'microphone_permission':'Requested by your browser or macOS when you turn on the microphone.',
   'privacy':'Audio stays on this device and is not saved. Only submitted text goes to the selected chat model.'}
 def _check(self,generation):
  if self._cancel.is_set() or self.store.setting('emergency_stop',False) or self.store.setting('stop_generation',0)!=generation:raise InterruptedError('Voice work stopped.')
 def _worker(self):
  path=self.root/'voice-worker-v1.py'
  if not path.exists() or path.read_text()!=WORKER:path.write_text(WORKER);path.chmod(0o600)
  return path
 def _spawn(self,cfg):
  kwargs={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
  return subprocess.Popen([str(self.python()),'-u',str(self._worker()),json.dumps(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,**kwargs)
 def setup(self,model='tiny'):
  self.store.require_desktop_owner()
  if model not in MODELS:raise ValueError('Choose Tiny, Small or Medium English streaming.')
  if self.store.setting('emergency_stop',False):raise ValueError('Resume tasks before setting up voice.')
  if shutil.disk_usage(self.root).free<1024**3:raise ValueError('Free at least 1 GiB for the speech runtime and models.')
  with self._lock:
   if self._setup:raise ValueError('Voice setup is already running.')
   self._cancel.clear();self._setup=True
   self.store.set_setting('voice_runtime',{**self.state(),'phase':'installing','progress':0,'error':None,'setup_model':model})
  threading.Thread(target=self._install,args=(model,self.store.setting('stop_generation',0)),daemon=True,name='voice-setup').start()
  return self.status()
 def _install(self,model,generation):
  process=None
  def check():self._check(generation)
  def update(**values):self.store.set_setting('voice_runtime',{**self.state(),**values})
  try:
   check();uv=shutil.which('uv') or ('/opt/homebrew/bin/uv' if Path('/opt/homebrew/bin/uv').is_file() else None)
   if not self.python().is_file():
    if uv:argv=[uv,'venv','--python','3.12',str(self.env)]
    else:
     python=shutil.which('python3') or shutil.which('python')
     if not python:raise ValueError('Install Python 3.12 or uv using Computer and app access, then retry Voice setup.')
     argv=[python,'-m','venv',str(self.env)]
    result=process_runtime.run(argv,run_id='voice-setup',timeout=300,check=check)
    if result['exit_code']:raise ValueError('Speech environment setup failed: '+result['stderr'][-1200:])
   argv=[uv,'pip','install','--python',str(self.python()),'moonshine-voice==0.1.6'] if uv else [str(self.python()),'-m','pip','install','moonshine-voice==0.1.6']
   result=process_runtime.run(argv,run_id='voice-setup',timeout=600,check=check)
   if result['exit_code']:raise ValueError('Speech runtime installation failed. This wheel needs macOS 15+ Apple silicon, Windows x64, or a supported Linux build. '+result['stderr'][-1000:])
   update(phase='downloading');process=self._spawn({'action':'setup','cache':str(self.root/'voice-models'),'arch':MODELS[model]['arch']})
   events=queue.Queue()
   def read():
    for line in process.stdout:
     try:events.put(json.loads(line))
     except ValueError:pass
    events.put(None)
   threading.Thread(target=read,daemon=True).start();verified=None;deadline=time.monotonic()+1200
   while True:
    check()
    if time.monotonic()>deadline:raise TimeoutError('Speech download timed out. Retry to reuse completed model files.')
    try:event=events.get(timeout=.1)
    except queue.Empty:continue
    if event is None:break
    if event['kind']=='progress':update(progress=round(event['fraction']*100),file=event.get('file'))
    if event['kind']=='verified':verified=event
    if event['kind']=='error':raise ValueError(event['error'])
   check()
   if not verified:raise ValueError('The speech worker exited before its local streaming test succeeded. Retry setup or choose the smaller model.')
   models={**self.state().get('models',{}),model:{'ready':True,'path':verified['path'],'load_ms':verified['load_ms'],'verification':'Local model loaded and processed silence; accent accuracy needs a speech test.'}}
   update(models=models,selected=model,phase='ready',progress=100,error=None)
  except Exception as error:update(phase='cancelled' if isinstance(error,InterruptedError) else 'failed',error=str(error))
  finally:
   if process:process_runtime.terminate(process)
   with self._lock:self._setup=False
 def start(self,model=None):
  self.store.require_desktop_owner();generation=self.store.setting('stop_generation',0)
  if self.store.setting('emergency_stop',False):raise ValueError('Resume tasks before listening.')
  model=model or self.state().get('selected','tiny');info=self.state().get('models',{}).get(model,{})
  if model not in MODELS or not info.get('ready') or not Path(info.get('path','')).is_dir():raise ValueError('Set up a local speech model in Settings → Voice first.')
  self._cancel.clear()
  with self._lock:
   if self._sessions:raise ValueError('A microphone session is already active. Turn it off before starting another.')
   from .store import new_id
   id=new_id('voice');process=self._spawn({'action':'stream','path':info['path'],'arch':MODELS[model]['arch']})
   session={'id':id,'process':process,'generation':generation,'events':[],'seq':0,'last_audio':time.monotonic(),'write_lock':threading.Lock(),'status':'loading'};self._sessions[id]=session
  def read():
   for line in process.stdout:
    try:event=json.loads(line)
    except ValueError:continue
    with self._lock:
     session['seq']+=1;session['events'].append({**event,'seq':session['seq']});session['events']=session['events'][-128:]
     if event['kind'] in {'ready','error','stopped'}:session['status']=event['kind']
   if session['status'] not in {'error','stopped'}:session['status']='error';session['events'].append({'kind':'error','error':'The local speech worker exited. Turn the mic off and retry.','seq':session['seq']+1})
  def expire():
   while process.poll() is None:
    time.sleep(1)
    if time.monotonic()-session['last_audio']>15 or self.store.setting('emergency_stop',False) or generation!=self.store.setting('stop_generation',0):self.stop(id);return
  threading.Thread(target=read,daemon=True,name='voice-events').start();threading.Thread(target=expire,daemon=True,name='voice-expiry').start()
  return {'id':id,'status':'loading','local':True,'model':model,'model_label':MODELS[model]['label']}
 def feed(self,id,pcm='',after=0):
  session=self._sessions.get(id)
  if not session:raise ValueError('Microphone session expired. Turn listening on again.')
  if self.store.setting('emergency_stop',False) or session['generation']!=self.store.setting('stop_generation',0):self.stop(id);raise InterruptedError('Listening stopped.')
  if not isinstance(pcm,str) or len(pcm)>90000:raise ValueError('Send at most one second of 16 kHz mono PCM.')
  if pcm:
   raw=base64.b64decode(pcm,validate=True)
   if len(raw)>64000 or len(raw)%2:raise ValueError('Invalid PCM audio packet.')
   with session['write_lock']:
    try:session['process'].stdin.write(json.dumps({'pcm':pcm})+'\n');session['process'].stdin.flush()
    except (BrokenPipeError,OSError):raise ValueError('The local voice worker stopped. Turn listening off and retry.')
  session['last_audio']=time.monotonic()
  with self._lock:return {'status':session['status'],'events':[e for e in session['events'] if e['seq']>int(after)]}
 def stop(self,id):
  with self._lock:session=self._sessions.pop(id,None)
  if session:
   try:
    with session['write_lock']:session['process'].stdin.write('{"stop":true}\n');session['process'].stdin.flush()
    session['process'].wait(timeout=2)
   except Exception:pass
   process_runtime.terminate(session['process'])
   return {'stopped':True,'events':session['events']}
  return {'stopped':True,'events':[]}
 def cancel_all(self):
  self._cancel.set();process_runtime.cancel('voice-setup')
  for id in list(self._sessions):self.stop(id)
 def dispatch(self,method,p):
  if method=='voice.status':return self.status()
  if method=='voice.setup':return self.setup(**p)
  if method=='voice.start':return self.start(**p)
  if method=='voice.feed':return self.feed(**p)
  if method=='voice.stop':return self.stop(**p)
  if method=='voice.cancel':self.cancel_all();return self.status()
  if method=='voice.select':
   if p.get('model') not in MODELS:raise ValueError('Choose a listed model.')
   info=self.state().get('models',{}).get(p['model'],{})
   if not info.get('ready') or not Path(info.get('path','')).is_dir():raise ValueError('Set up and verify this speech model before selecting it.')
   if self._sessions:raise ValueError('Turn the microphone off before switching speech models.')
   self.store.set_setting('voice_runtime',{**self.state(),'selected':p['model']});return self.status()
  raise ValueError('Unknown local speech action.')
