#!/usr/bin/env python3
"""Run a genuine stdio MCP self-test without touching any external processes.

Run on your Mac to verify the actual Mach VM memory backend; Linux uses procfs
for development verification. This checks the bridge, NOT Cheat Engine/game access.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'src-tauri/resources/mac_memory_mcp.py'


def run():
    if not SERVER.is_file():
        raise RuntimeError('Bundled memory helper source is missing')
    with subprocess.Popen([sys.executable, '-u', str(SERVER)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            for request in [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                 'params': {'name': 'bridge_self_test', 'arguments': {}}},
            ]:
                child.stdin.write(json.dumps(request) + '\n')
                child.stdin.flush()
                line = child.stdout.readline()
                if not line:
                    raise RuntimeError('Memory helper exited before answering')
                response = json.loads(line)
                if response.get('id') != request['id'] or 'error' in response:
                    raise RuntimeError(f'Invalid MCP reply: {response}')
                if request['id'] == 1:
                    assert response['result']['serverInfo']['name'] == 'bughunter-mac-memory'
                elif request['id'] == 2:
                    assert 'bridge_self_test' in {t['name'] for t in response['result']['tools']}
                else:
                    result = response['result']
                    text = result['content'][0]['text']
                    if result.get('isError'):
                        raise RuntimeError(text)
                    outcome = json.loads(text)
                    if outcome.get('passed') is not True or outcome.get('external_process_touched') is not False:
                        raise RuntimeError('Self-test did not prove a verified, isolated read/write cycle')
                    if outcome.get('steps') != [
                            'read owned value', 'write replacement',
                            'confirm readback', 'restore original']:
                        raise RuntimeError('Self-test did not confirm the expected steps')
                    return outcome
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


def main():
    try:
        result = run()
    except Exception as error:
        print(json.dumps({'passed': False, 'error': str(error),
                          'note': 'No external process access or Cheat Engine control was tested.'}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
