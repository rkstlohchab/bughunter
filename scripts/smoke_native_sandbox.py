#!/usr/bin/env python3
"""Manual native host smoke check, on the Mac or Linux machine running the app.

Does not modify user files or contact the internet. Exits nonzero when sandbox
execution is unavailable or cannot enforce read-only scope and network denial.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from bughunter.sandbox import status,run

info=status()
print('Sandbox status:',info)
if not info['available']:
    print('SKIPPED: Native sandbox unavailable; refusing unsandboxed execution.')
    raise SystemExit(2)
with tempfile.TemporaryDirectory(prefix='bughunter-sandbox-test-') as directory:
    folder=Path(directory)
    (folder/'canary.txt').write_text('WORKSPACE-CANARY',encoding='utf-8')
    script='''from pathlib import Path
import socket
print('WORKSPACE:',Path('canary.txt').read_text())
try:
    socket.create_connection(('1.1.1.1',443),timeout=2)
    print('NETWORK-UNEXPECTED-ACCESS')
except (OSError, PermissionError):
    print('NETWORK-DENIED')
'''
    result=run(str(folder),script,approved=True)
    print('Sandbox exit code:',result['exit_code'])
    print('Output:',result['stdout'])
    if result['stderr']:
        print('Errors:',result['stderr'])
    if (result['exit_code']!=0 or 'WORKSPACE: WORKSPACE-CANARY' not in result['stdout']
            or 'NETWORK-DENIED' not in result['stdout'] or 'NETWORK-UNEXPECTED-ACCESS' in result['stdout']):
        raise SystemExit('FAILED: Native sandbox needs host-specific repair; do not use it for untrusted scripts.')
print('PASS: workspace was readable and a network connection was denied in this test.')
