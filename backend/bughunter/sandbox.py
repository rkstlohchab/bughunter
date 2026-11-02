"""Fail-closed OS sandbox for explicit local Python research scripts.

Linux: bubblewrap user/mount/pid/network namespaces.
macOS: sandbox-exec Seatbelt profile *if available* (deprecated on macOS).
Windows: deliberately unsupported until a verified AppContainer/VM runner exists.
This does NOT sandbox the separate Claude/Codex CLIs; they retain their own
sandbox controls. No unrestricted PC command execution is exposed here.
"""
from __future__ import annotations
import os
import signal
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_CODE=16_000


def status():
    system=platform.system()
    name='bubblewrap' if system=='Linux' else 'sandbox-exec' if system=='Darwin' else ''
    path=shutil.which('bwrap' if system=='Linux' else 'sandbox-exec') if name else None
    return {'platform':system,'available':bool(path),'backend':name or 'none',
      'supported':'experimental' if system=='Darwin' and path else 'supported' if path else 'unavailable',
      'limitations':('macOS sandbox-exec is deprecated and needs real-device verification.' if system=='Darwin'
           else 'Windows requires a separate AppContainer or VM integration.' if system=='Windows'
           else 'Bubblewrap needs working user namespaces; availability does not prove operation.'),
      'agent_clis_isolated':False}


def _scheme_quote(path):
    return '"'+str(path).replace('\\','\\\\').replace('"','\\"')+'"'


def command(workspace, code, python_executable=None):
    root=Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():raise ValueError('Workspace must be a directory.')
    if not isinstance(code,str) or not code.strip() or len(code)>MAX_CODE:
        raise ValueError('Provide up to 16,000 characters of local Python code.')
    default_python = (sys.executable if not getattr(sys, 'frozen', False) and 'python' in Path(sys.executable).name.lower()
                      else shutil.which('python3'))
    if not (python_executable or default_python):
        raise ValueError('Install Python 3 to run sandboxed local analysis.')
    python=Path(python_executable or default_python).resolve(strict=True)
    # Apply resource limits inside the sandboxed child interpreter, not with a
    # preexec_fn in our multithreaded desktop backend (unsafe after fork).
    bootstrap=("import resource\n"
               "resource.setrlimit(resource.RLIMIT_CPU,(12,12))\n"
               "resource.setrlimit(resource.RLIMIT_FSIZE,(2000000,2000000))\n"
               "resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))\n"
               "exec(compile("+repr(code)+",'<sandbox-task>','exec'))\n")
    system=platform.system()
    if system=='Linux':
        bwrap=shutil.which('bwrap')
        if not bwrap:raise ValueError('bubblewrap is missing; sandbox execution is disabled.')
        args=[bwrap,'--die-with-parent','--new-session','--unshare-all','--unshare-net',
             '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--tmpfs','/home']
        for base in ('/usr','/bin','/sbin','/lib','/lib64','/etc','/opt'):
            if Path(base).exists():args.extend(['--ro-bind',base,base])
        args.extend(['--ro-bind',str(root),'/workspace','--chdir','/workspace',
             '--clearenv','--setenv','PATH','/usr/local/bin:/usr/bin:/bin',
             '--setenv','HOME','/home','--setenv','PYTHONDONTWRITEBYTECODE','1',
             str(python),'-I','-B','-c',bootstrap])
        return args,None
    if system=='Darwin':
        runner=shutil.which('sandbox-exec')
        if not runner:raise ValueError('Native sandbox-exec unavailable. No unsandboxed fallback.')
        # A deliberately narrow Seatbelt profile. No network and no home access.
        # Exposing workspace read-only even if it contains malicious scripts;
        # -I isolates Python import paths and -B avoids bytecode writes.
        profile='''(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow sysctl-read)
(allow file-read* (subpath "/usr") (subpath "/System") (subpath "/Library")
 (subpath "/private/etc") (subpath "/private/var/db/dyld") (subpath "/opt/homebrew")
 (subpath %s))
(allow file-write* (subpath "/private/tmp") (subpath "/tmp"))
''' % _scheme_quote(root)
        args=[runner,'-p',profile,str(python),'-I','-B','-c',bootstrap]
        return args,None
    raise ValueError('No verified OS sandbox is configured for this platform. Execution blocked.')


def run(workspace,code,approved=False):
    if approved is not True:
        raise ValueError('Explicit operator approval is required for sandboxed script execution.')
    args,_=command(workspace,code)
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
    with tempfile.TemporaryFile(mode='w+b') as stdout, tempfile.TemporaryFile(mode='w+b') as stderr:
        proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                              env=env,cwd=str(Path(workspace).expanduser().resolve()),
                              start_new_session=True)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait(timeout=3)
            raise ValueError('Sandboxed task exceeded the 15-second limit.') from None
        stdout.seek(0);stderr.seek(0)
        return {'exit_code':proc.returncode,
                'stdout':stdout.read(15_000).decode('utf-8','replace'),
                'stderr':stderr.read(3_000).decode('utf-8','replace'),
                'sandbox':status()['backend'],
                'note':'Sandboxed local analysis; network and outside-workspace file access are denied by the native profile.'}
