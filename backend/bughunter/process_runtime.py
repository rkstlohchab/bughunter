"""Owned child processes with bounded receipts and cancellation on every desktop OS."""
from __future__ import annotations
import os
from pathlib import Path
import signal
import re
import shutil
import subprocess
import tempfile
import threading
import time

_LOCK=threading.RLock()
_ACTIVE={}


def cli_argv(argv,*,windows=None):
    """Use Node directly for npm's Windows shims; never pass model text through cmd.exe."""
    if windows is None:windows=os.name=='nt'
    if not windows or not argv[0].lower().endswith(('.cmd','.bat')):return argv
    wrapper=Path(argv[0])
    try:source=wrapper.read_text(encoding='utf-8-sig')
    except OSError as error:raise ValueError('Could not read this CLI launcher. Install its native executable.') from error
    for match in re.finditer(r'%dp0%[\\/](node_modules[\\/][^"\r\n]+\.(?:js|cjs|mjs))',source,re.I):
        script=(wrapper.parent/match[1].replace('\\','/')).resolve()
        if script.is_relative_to(wrapper.parent.resolve()) and script.is_file():
            node=shutil.which('node')
            if node:return [node,str(script),*argv[1:]]
    raise ValueError('This Windows CLI shim is unsupported. Install Node.js LTS and the official npm CLI, or configure a native executable.')


def terminate(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
    else:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:return
        try:process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill()


def cancel(run_id=None):
    with _LOCK:owned=[p for p,owner in _ACTIVE.values() if run_id is None or owner==run_id]
    for process in owned:terminate(process)


def run(argv,*,run_id=None,cwd=None,timeout=60,env=None,input=None,check=None,output_limit=1_048_576,shell=False):
    if not shell and (not isinstance(argv,list) or not argv or any(not isinstance(arg,str) for arg in argv)):
        raise ValueError('Use an executable and separate string arguments.')
    timeout=max(1,min(float(timeout),1800));started=time.monotonic()
    if not shell:argv=cli_argv(argv)
    if check:check()
    kwargs={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr,tempfile.TemporaryFile() as stdin:
        if input is not None:stdin.write(input.encode('utf-8') if isinstance(input,str) else input);stdin.seek(0)
        process=subprocess.Popen(argv,cwd=cwd,env=env,stdin=stdin,stdout=stdout,stderr=stderr,shell=shell,**kwargs)
        with _LOCK:_ACTIVE[process.pid]=(process,run_id)
        try:
            while process.poll() is None:
                if check:check()
                if time.monotonic()-started>timeout:raise TimeoutError('Command timed out; inspect the saved state before retrying.')
                time.sleep(.08)
            if check:check()
            def tail(stream):
                length=stream.seek(0,2);stream.seek(max(0,length-output_limit));return stream.read().decode('utf-8','replace'),length>output_limit
            out,out_cut=tail(stdout);err,err_cut=tail(stderr)
            return {'exit_code':process.returncode,'stdout':out,'stderr':err,'truncated':out_cut or err_cut,'duration_ms':round((time.monotonic()-started)*1000)}
        except BaseException:
            terminate(process)
            raise
        finally:
            with _LOCK:_ACTIVE.pop(process.pid,None)
