"""Explicitly approved, allowlisted Homebrew installs. Never runs a shell string.

This can help a Mac user install *free* supporting tools. It cannot install or
license Burp Pro or guarantee Wine/Windows program compatibility.
"""
from __future__ import annotations
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from .store import now

PACKAGES = {
    'ffmpeg':{'label':'Video processing','formula':'ffmpeg','binaries':['ffmpeg','ffprobe'],'description':'Extract screenshots and audio from imported videos.'},
    'ollama':{'label':'Local AI models','formula':'ollama','binaries':['ollama'],'description':'Run compatible downloaded local AI models.'},
    'whisper-cpp':{'label':'Speech transcription','formula':'whisper-cpp','binaries':['whisper-cli'],'description':'Transcribe audio locally (model weights still required).'},
    'jadx':{'label':'Android APK inspector','formula':'jadx','binaries':['jadx'],'description':'Inspect Android packages you own or may analyze.'},
    'apktool':{'label':'Android resource inspector','formula':'apktool','binaries':['apktool'],'description':'Inspect APK resources without executing the app.'},
    'ghidra':{'label':'Ghidra reverse-engineering app','cask':'ghidra','binaries':[],'description':'Desktop reverse-engineering tool; check the macOS release requirements.'},
}

class ToolInstaller:
    def __init__(self,store):
        self.store=store
        self._lock=threading.RLock()
        self._running=set()
        for old in self.store.list('setup_job'):
            if old.get('status')=='running':
                self.store.put('setup_job',{**old,'status':'interrupted',
                   'message':'The previous desktop session closed; check Homebrew and recheck installed tools.'},id=old['id'])

    def catalog(self):
        supported=platform.system()=='Darwin'
        brew=shutil.which('brew')
        return {'supported':supported,'homebrew_available':bool(brew), 'items':[
            {'id':id,'label':p['label'],'description':p['description'],
             'installed':all(shutil.which(binary) for binary in p['binaries']) if p['binaries'] else any((root/'Ghidra.app').is_dir() for root in (Path('/Applications'),Path.home()/'Applications')),
             'status':'Check app manually after installation' if not p['binaries'] else 'CLI executable checked',
             'command':'brew install '+('--cask ' if p.get('cask') else '')+(p.get('cask') or p.get('formula'))}
            for id,p in PACKAGES.items()]}

    def install(self, package, approved=False):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False): raise ValueError('Task execution is stopped.')
        if approved is not True: raise ValueError('Review and approve the installation first.')
        if package not in PACKAGES:raise ValueError('Not an approved installation package.')
        if platform.system()!='Darwin':raise ValueError('Automatic installation is only available on macOS.')
        brew=shutil.which('brew')
        if not brew:raise ValueError('Install Homebrew first from https://brew.sh/')
        with self._lock:
            if self._running:raise ValueError('Another installation is already running.')
            job=self.store.put('setup_job',{'package':package,'status':'running','started_at':now(),
                                             'message':'Homebrew installation started after your approval.',
                                             'command':'brew install '+('--cask ' if 'cask' in PACKAGES[package] else '')+package})
            self._running.add(job['id'])
        thread=threading.Thread(target=self._run,args=(job['id'],package,brew),daemon=True)
        thread.start()
        return job

    def _run(self,id,package,brew):
        record=self.store.get('setup_job',id)
        pkg=PACKAGES[package]
        args=[brew,'install']+(['--cask',pkg['cask']] if 'cask' in pkg else [pkg['formula']])
        try:
            result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                   text=True,timeout=1800,check=False,env=None)
            output=(result.stdout or '')[-5000:]
            status='completed' if result.returncode==0 else 'failed'
            message=('Homebrew finished. Recheck installation status.' if result.returncode==0 else
                     'Homebrew returned a non-zero exit code; review the output.')
            self.store.put('setup_job',{**record,'status':status,'exit_code':result.returncode,
                                        'message':message,'output':output,'finished_at':now()},id=id)
        except subprocess.TimeoutExpired:
            self.store.put('setup_job',{**record,'status':'failed','message':'Homebrew timed out after 30 minutes.',
                                        'finished_at':now()},id=id)
        except Exception as exc:
            self.store.put('setup_job',{**record,'status':'failed','message':type(exc).__name__,
                                        'finished_at':now()},id=id)
        finally:
            with self._lock:self._running.discard(id)

    def dispatch(self, method,params):
        if method=='setup.catalog':return self.catalog()
        if method=='setup.install':return self.install(params.get('package'),params.get('approved') is True)
        if method=='setup.status':
            job=self.store.get('setup_job',str(params.get('id') or ''))
            if not job:raise ValueError('Installation not found.')
            return job
        if method=='setup.history':return self.store.list('setup_job')[:30]
        raise ValueError('Unknown installation method.')
