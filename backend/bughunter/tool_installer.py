"""Platform-specific dependency plans, observed install results and cancellable jobs."""
from __future__ import annotations
import os
import platform
import shutil
import threading
import time
import re
import hashlib
from pathlib import Path
from .store import now
from . import process_runtime

PACKAGES={
 'rea':{'label':'REA reverse engineering','description':'Version-pinned REA MCP tools, managed by the app for every model. Native engines are installed separately.','binaries':[],'managed_rea':True},
 'browser-runtime':{'label':'Background browser runtime','description':'Playwright Chromium headless shell for task-owned background web pages.','binaries':[],'playwright':True},
 'agent-desktop':{'label':'Desktop control engine','description':'Vercel agent-desktop 1.3.0. Native controls depend on OS permissions.','binaries':['agent-desktop'],'npm':'agent-desktop@1.3.0'},
 'adb':{'label':'Android device connection','description':'Discover and connect Android devices; no APK file required.','binaries':['adb'],'cask':'android-platform-tools','winget':'Google.PlatformTools','apt':'adb','dnf':'android-tools','pacman':'android-tools'},
 'scrcpy':{'label':'Android screen mirroring','description':'Mirror a connected Android screen after device debugging authorization.','binaries':['scrcpy'],'formula':'scrcpy','winget':'Genymobile.scrcpy','apt':'scrcpy','dnf':'scrcpy','pacman':'scrcpy'},
 'ffmpeg':{'label':'Video processing','description':'Extract screenshots and audio from imported videos.','binaries':['ffmpeg','ffprobe'],'formula':'ffmpeg','winget':'Gyan.FFmpeg','apt':'ffmpeg','dnf':'ffmpeg-free','pacman':'ffmpeg'},
 'ollama':{'label':'Local AI models','description':'Run compatible downloaded models.','binaries':['ollama'],'formula':'ollama','winget':'Ollama.Ollama','pacman':'ollama'},
 'whisper-cpp':{'label':'Speech transcription','description':'Local transcription; model weights are a separate download.','binaries':['whisper-cli'],'formula':'whisper-cpp'},
 'jadx':{'label':'Android package inspector','description':'Inspect an APK when package analysis is requested.','binaries':['jadx'],'formula':'jadx','pacman':'jadx'},
 'apktool':{'label':'Android resource inspector','description':'Inspect Android package resources.','binaries':['apktool'],'formula':'apktool','apt':'apktool','pacman':'apktool'},
 'ghidra':{'label':'Ghidra','description':'Desktop binary analysis; Java and platform requirements apply.','binaries':[],'formula':'ghidra','pacman':'ghidra'},
}


def executable(name):
    candidates=[shutil.which(name)]
    if platform.system()=='Darwin':candidates += ['/opt/homebrew/bin/'+name,'/usr/local/bin/'+name,str(Path.home()/'.npm-global/bin'/name)]
    if os.name=='nt':candidates += [str(Path(os.environ.get('APPDATA',''))/'npm'/(name+'.cmd'))]
    return next((str(p) for p in candidates if p and Path(p).is_file()),None)


class ToolInstaller:
    def __init__(self,store,recover=False):
        self.store=store;self._lock=threading.RLock();self._running=set();self._cancelled=set()
        if recover:
            for old in self.store.list('setup_job'):
                if old.get('status')=='running':self.store.put('setup_job',{**old,'status':'interrupted','message':'The desktop restarted. Recheck the installed tools before retrying.'},id=old['id'])

    def package(self,package):
        if package in PACKAGES:return PACKAGES[package]
        row=self.store.get('registry_package',str(package))
        if not row or row.get('platform')!=platform.system():raise ValueError('Choose a supported package, or search the system package registry and select an exact result first.')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+/@-]{0,180}',row.get('identifier','')):raise ValueError('Invalid package registry identifier.')
        return row

    def search(self,query):
        if not isinstance(query,str) or not 2<=len(query.strip())<=80:raise ValueError('Enter an app name between 2 and 80 characters.')
        query=query.strip();system=platform.system();results=[]
        if system=='Darwin':
            manager=executable('brew')
            if not manager:raise ValueError('Homebrew is missing. Install it from brew.sh, then retry.')
            for kind in ('formula','cask'):
                result=process_runtime.run([manager,'search','--'+kind,'--',re.escape(query)],timeout=45)
                for token in result['stdout'].split():
                    token=token.rstrip('✓')
                    if re.fullmatch(r'[a-z0-9][a-z0-9@+./_-]*',token) and query.casefold() in token.casefold():results.append((token,kind,token))
        elif system=='Windows':
            manager=executable('winget')
            if not manager:raise ValueError('Install Windows App Installer to search winget.')
            result=process_runtime.run([manager,'search','--query',query,'--accept-source-agreements','--disable-interactivity'],timeout=45)
            for line in result['stdout'].splitlines():
                fields=re.split(r'\s{2,}',line.strip())
                if len(fields)>=2 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*\.[A-Za-z0-9._+-]+',fields[1]):results.append((fields[1],'winget',fields[0]))
        else:
            manager=executable('apt-cache') or executable('dnf') or executable('pacman')
            if not manager:raise ValueError('No supported Linux package registry was detected.')
            kind='apt' if Path(manager).name=='apt-cache' else Path(manager).name
            argv=[manager,'search',query] if kind in {'apt','dnf'} else [manager,'-Ss',query]
            result=process_runtime.run(argv,timeout=45)
            for line in result['stdout'].splitlines():
                if kind=='apt':
                    fields=line.split(' - ',1)
                    if len(fields)==2:results.append((fields[0],'apt',fields[0]+' · '+fields[1][:100]))
                elif kind=='pacman' and '/' in line and not line.startswith(' '):results.append((line.split()[0].split('/',1)[1],kind,line.split()[0]))
                elif kind=='dnf' and ' : ' in line:results.append((line.split()[0].rsplit('.',1)[0],kind,line[:120]))
        for identifier,kind,label in results[:30]:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+/@-]{0,180}',identifier):continue
            id='registry_'+hashlib.sha256((system+':'+kind+':'+identifier).encode()).hexdigest()[:24]
            self.store.put('registry_package',{'identifier':identifier,'platform':system,'label':label,kind:identifier,'binaries':[],'source':'System package registry'},id=id)
        return {'items':[{'id':'registry_'+hashlib.sha256((system+':'+kind+':'+identifier).encode()).hexdigest()[:24],'label':label,'identifier':identifier,'kind':kind} for identifier,kind,label in results[:30] if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+/@-]{0,180}',identifier)],'source':'System package registry','detail':'Review the exact publisher/package before installation.'}

    def installed(self,package):
        pkg=self.package(package)
        if pkg.get('managed_rea'):return (self.store.root/'runtimes/rea/node_modules/rea-agents/scripts/rea.mjs').is_file()
        if pkg.get('playwright'):
            from .browser_workspace import configure_browser_cache
            configure_browser_cache()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                exe=Path(p.chromium.executable_path)
                cache=next((parent for parent in exe.parents if parent.name=='ms-playwright'),exe.parent.parent)
                return exe.is_file() or any(cache.glob('chromium_headless_shell-*/*/headless_shell')) or any(cache.glob('chromium_headless_shell-*/*/chrome-headless-shell.exe')) or any(cache.glob('chromium_headless_shell-*/*/chrome-headless-shell'))
        if package not in PACKAGES:
            if platform.system()=='Darwin':argv=[executable('brew'),'list','--versions',pkg['identifier']]
            elif platform.system()=='Windows':argv=[executable('winget'),'list','--exact','--id',pkg['identifier'],'--disable-interactivity','--accept-source-agreements']
            elif pkg.get('apt'):argv=[executable('dpkg-query'),'-W','-f=${Status}',pkg['identifier']]
            elif pkg.get('dnf'):argv=[executable('rpm'),'-q',pkg['identifier']]
            else:argv=[executable('pacman'),'-Q',pkg['identifier']]
            if not argv[0]:return False
            result=process_runtime.run(argv,timeout=15)
            return result['exit_code']==0 and bool(result['stdout'].strip()) and (not pkg.get('apt') or 'install ok installed' in result['stdout'])
        if pkg['binaries']:return all(executable(binary) for binary in pkg['binaries'])
        if package=='ghidra' and platform.system()=='Darwin':return bool(executable('ghidraRun')) or any((root/'Ghidra.app').is_dir() for root in (Path('/Applications'),Path.home()/'Applications'))
        return bool(executable('ghidraRun') or executable('ghidraRun.bat'))

    def plan(self,package):
        pkg=self.package(package);system=platform.system();argv=[];detail='';manager=None
        if pkg.get('managed_rea'):
            from .rea import REA_VERSION
            manager=executable('npm')
            if manager:argv=[manager,'install','--prefix',str(self.store.root/'runtimes/rea'),'--ignore-scripts','--no-audit','--no-fund','--save-exact','rea-agents@'+REA_VERSION]
            else:detail='Install a supported Node.js and npm runtime, then retry REA setup.'
        elif pkg.get('playwright'):
            from playwright._impl._driver import compute_driver_executable
            node,cli=compute_driver_executable();manager=node;argv=[str(node),str(cli),'install','chromium','--only-shell']
        elif pkg.get('npm'):
            manager=executable('npm')
            if manager:
                argv=[manager,'install','--global',pkg['npm']]
            else:detail='Install Node.js LTS from https://nodejs.org/ and restart the app so npm is on PATH.'
        elif system=='Darwin':
            manager=executable('brew')
            if manager:argv=[manager,'install']+(['--cask',pkg['cask']] if pkg.get('cask') else [pkg['formula']])
            else:detail='Homebrew is missing. Install it from https://brew.sh/ and recheck.'
        elif system=='Windows':
            manager=executable('winget')
            if manager and pkg.get('winget'):argv=[manager,'install','--exact','--id',pkg['winget'],'--accept-source-agreements','--accept-package-agreements','--disable-interactivity']
            else:detail='This dependency needs Windows App Installer (winget), or a manual install from the publisher.'
        elif system=='Linux':
            for name in ('apt-get','dnf','pacman'):
                candidate=executable(name);key={'apt-get':'apt','dnf':'dnf','pacman':'pacman'}[name]
                if candidate and pkg.get(key):
                    manager=candidate;argv=[candidate]+(['-S','--noconfirm',pkg[key]] if name=='pacman' else ['install','-y',pkg[key]])
                    if hasattr(os,'geteuid') and os.geteuid()!=0:
                        pkexec=executable('pkexec');sudo=executable('sudo')
                        if pkexec:argv=[pkexec,*argv]
                        elif sudo:argv=[sudo,'-n',*argv]
                        else:argv=[];detail='An administrator must install this dependency; sudo is unavailable.'
                    break
            if not argv and not detail:detail='No supported package mapping exists for this Linux distribution. Install the publisher’s package and recheck.'
        else:detail='This operating system has no automatic installation plan.'
        free=shutil.disk_usage(self.store.root).free
        if free<1024**3:detail='Free at least 1 GiB before installing dependencies.'
        return {'package':package,'label':pkg['label'],'platform':system,'installed':self.installed(package),'supported':bool(argv),
                'manager':manager,'argv':argv,'command':__import__('shlex').join(argv),'free_bytes':free,
                'ready_to_install':bool(argv) and free>=1024**3,'detail':detail or 'Uses the platform package manager. Administrator prompts may require you.',
                'source':'https://github.com/vercel-labs/agent-desktop' if package=='agent-desktop' else 'Platform package manager catalog'}

    def catalog(self):
        items=[]
        for id,pkg in PACKAGES.items():
            plan=self.plan(id)
            items.append({'id':id,'label':pkg['label'],'description':pkg['description'],'installed':plan['installed'],
                          'status':'Executable found' if plan['installed'] else plan['detail'],'command':plan['command'],'supported':plan['supported']})
        return {'supported':platform.system() in {'Darwin','Windows','Linux'},'homebrew_available':bool(executable('brew')),'items':items}

    def install(self,package,approved=False,run_id=None):
        self.store.require_desktop_owner()
        if approved is not True:raise ValueError('Review and approve the installation first.')
        plan=self.plan(package)
        if not plan['ready_to_install']:raise ValueError(plan['detail'])
        with self._lock:
            if self.store.setting('emergency_stop',False):raise ValueError('Task execution is stopped.')
            if self._running:raise ValueError('Another installation is already running. Wait for its result.')
            generation=self.store.setting('stop_generation',0)
            job=self.store.put('setup_job',{'package':package,'run_id':run_id,'status':'running','phase':'package_manager','started_at':now(),'started_epoch':time.time(),
                'message':'Installing with '+str(plan['manager']),'command':plan['command'],'plan':plan})
            self._running.add(job['id'])
        threading.Thread(target=self._run,args=(job['id'],plan['argv'],generation),daemon=True,name='setup:'+package).start()
        return job

    def cancel(self,id):
        with self._lock:self._cancelled.add(id)
        process_runtime.cancel('setup:'+id)
        return {'cancel_requested':True,'id':id}

    def cancel_all(self):
        with self._lock:ids=list(self._running);self._cancelled.update(ids)
        for id in ids:process_runtime.cancel('setup:'+id)

    def _run(self,id,argv,generation):
        record=self.store.get('setup_job',id)
        def check():
            with self._lock:cancelled=id in self._cancelled
            if cancelled or self.store.setting('emergency_stop',False) or generation!=self.store.setting('stop_generation',0):raise InterruptedError('Installation stopped. Recheck before retrying.')
            if record.get('run_id'):
                run=self.store.get('operator_run',record['run_id']) or {}
                if run.get('status')=='cancelled':raise InterruptedError('Task cancelled its installation.')
        try:
            result=process_runtime.run(argv,run_id='setup:'+id,timeout=1800,check=check,output_limit=12000)
            check();installed=self.installed(record['package'])
            status='completed' if result['exit_code']==0 else 'failed'
            message=('Package manager completed. Executable found.' if installed else 'Package manager completed. Restart the app or check PATH to verify the executable.') if status=='completed' else 'Installation failed. Review the output; an administrator or updated package index may be needed.'
            self.store.put('setup_job',{**record,**result,'status':status,'installed_verified':installed,'phase':'finished','message':message,'output':result['stdout']+result['stderr'],'finished_at':now()},id=id)
        except Exception as error:
            status='cancelled' if isinstance(error,InterruptedError) else 'failed'
            self.store.put('setup_job',{**record,'status':status,'phase':'finished','message':str(error),'finished_at':now()},id=id)
        finally:
            with self._lock:self._running.discard(id);self._cancelled.discard(id)

    def dispatch(self,method,params):
        if method=='setup.search':return self.search(params.get('query'))
        if method=='setup.catalog':return self.catalog()
        if method=='setup.plan':return self.plan(params['package'])
        if method=='setup.install':return self.install(params.get('package'),params.get('approved') is True)
        if method=='setup.cancel':return self.cancel(params['id'])
        if method=='setup.status':
            job=self.store.get('setup_job',str(params.get('id') or ''))
            if not job:raise ValueError('Installation not found.')
            return {**job,'elapsed_seconds':round(time.time()-job.get('started_epoch',time.time())) if job['status']=='running' else None}
        if method=='setup.history':return self.store.list('setup_job')[:30]
        raise ValueError('Unknown installation method.')
