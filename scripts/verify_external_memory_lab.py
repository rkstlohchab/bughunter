#!/usr/bin/env python3
"""Actual external-process memory test against ONLY the bundled lab program.

To avoid surprise process modifications the default is read-only. --write-lab
only changes the disposable fixture process created by this script.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def rpc(stream, method: str, params=None, seq=[0]):
    seq[0]+=1
    stream.stdin.write(json.dumps({'jsonrpc':'2.0','id':seq[0],'method':method,'params':params or {}})+'\n')
    stream.stdin.flush()
    for line in stream.stdout:
        msg=json.loads(line)
        if msg.get('id')==seq[0]:
            if 'error' in msg: raise RuntimeError(str(msg['error']))
            return msg.get('result')
    raise RuntimeError('Memory helper exited without a response.')


def call(stream, tool, args):
    result=rpc(stream,'tools/call',{'name':tool,'arguments':args})
    message='\n'.join(x.get('text','') for x in result.get('content',[]))
    if result.get('isError'):raise RuntimeError(f'{tool}: {message}')
    return message


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--write-lab',action='store_true',help='Modify ONLY this script\'s disposable, owned test process')
    args=parser.parse_args()
    if sys.platform!='darwin':
        print('SKIP: external macOS process attachment is testable only on Mac.');return 2
    fixture=subprocess.Popen([sys.executable,'-u',str(ROOT/'examples/offline_memory_lab.py')],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    helper=subprocess.Popen([sys.executable,'-u',str(ROOT/'src-tauri/resources/mac_memory_mcp.py')],
                            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        context=json.loads(fixture.stdout.readline())
        print('Test target: disposable lab pid',context['pid'])
        print('Attach:',call(helper,'attach_process',{'pid':context['pid']}))
        print('Read:',call(helper,'read_memory',{'address':context['address'],'type':'i32','size':4}))
        if args.write_lab:
            print('Changing only the lab value and verifying readback…')
            print(call(helper,'write_memory',{'address':context['address'],'type':'i32','value':13371399}))
            fixture.stdin.write('status\n');fixture.stdin.flush()
            current=json.loads(fixture.stdout.readline())
            if current['money']!=13371399:raise AssertionError('Target did not reflect the written value')
            print('PASS: external process write readback confirmed by target itself.')
            # Restore the original before exit.
            print(call(helper,'write_memory',{'address':context['address'],'type':'i32','value':13371337}))
        else:
            print('PASS: lab attached and read. No external memory was modified.')
        return 0
    except Exception as error:
        print(f'FAIL: {error}\nExternal attachment may require macOS debugger entitlements. Never disable SIP to run this test.',file=sys.stderr)
        return 1
    finally:
        for proc in (helper,fixture):
            try:
                if proc.stdin:proc.stdin.close()
                proc.terminate();proc.wait(timeout=3)
            except Exception:proc.kill()

if __name__=='__main__':raise SystemExit(main())
