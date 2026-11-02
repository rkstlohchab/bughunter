#!/usr/bin/env python3
"""End-to-end read-only restart check on two independently spawned offline labs.

No third-party process is targeted. Optional --write-lab changes ONLY a child
process created by this script and restores the original before continuing.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

class Bridge:
    def __init__(self, proc):
        self.proc = proc
        self.sequence = 0

    def call(self, name, args):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'id': self.sequence, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': args}}
        self.proc.stdin.write(json.dumps(request) + '\n');self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Memory backend exited before returning a tool response.')
        msg = json.loads(line)
        if msg.get('id') != self.sequence or 'error' in msg:
            raise RuntimeError(str(msg.get('error') or 'Mismatched RPC response'))
        result = msg.get('result') or {}
        out = '\n'.join(part.get('text','') for part in result.get('content',[]))
        if result.get('isError'):
            raise RuntimeError(out)
        return out


def once(write_lab=False):
    fixture = subprocess.Popen([sys.executable, '-u', str(ROOT / 'examples/offline_memory_lab.py')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    helper = subprocess.Popen([sys.executable, '-u', str(ROOT / 'src-tauri/resources/mac_memory_mcp.py')],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    bridge = Bridge(helper)
    touched = False
    original = 100
    try:
        manifest = json.loads(fixture.stdout.readline())
        bridge.call('attach_process', {'pid': manifest['pid']})
        check = json.loads(bridge.call('verify_pointer_chain', {
            'base': manifest['health_pointer_slot'], 'offsets': [0],
            'type': 'i32', 'expected_value': original}))
        if not check.get('matched'):
            raise AssertionError('Health pointer did not resolve to the expected value.')
        entities = json.loads(bridge.call('read_entity_layout', {
            'base': manifest['entity_base'], **manifest['entity_layout']}))
        if len(entities['entities']) != 4:
            raise AssertionError('Read the wrong number of game entities.')
        samples = json.loads(bridge.call('watch_value', {
            'base': manifest['health_pointer_slot'], 'offsets': [0],
            'type': 'i32', 'samples': 3, 'interval_s': .1}))
        if any(s['value'] != original for s in samples['samples']):
            raise AssertionError('Health monitor returned an unexpected value.')
        if write_lab:
            bridge.call('write_memory', {'address': manifest['health_address'], 'type': 'i32', 'value': 160})
            touched = True
            fixture.stdin.write('status\n');fixture.stdin.flush()
            target = json.loads(fixture.stdout.readline())
            if target['health'] != 160:
                raise AssertionError('Target process did not observe the memory modification.')
        return {'pid': manifest['pid'], 'process_instance_id': check['process_instance_id'],
                'health_pointer_slot': manifest['health_pointer_slot'],
                'resolved_address': check['resolved_address'],
                'verified_health': check['matched'], 'entity_count': len(entities['entities']),
                'samples': samples['samples'], 'write_verified': bool(write_lab)}
    finally:
        if touched:
            try:
                bridge.call('write_memory', {'address': manifest['health_address'], 'type': 'i32', 'value': original})
                fixture.stdin.write('status\n');fixture.stdin.flush()
                restored = json.loads(fixture.stdout.readline())
                if restored['health'] != original:
                    print('WARNING: lab restoration did not verify', file=sys.stderr)
            except Exception as exc:
                print('WARNING: could not restore disposable lab:', exc, file=sys.stderr)
        for proc in (helper, fixture):
            try:
                proc.terminate();proc.wait(timeout=3)
            except Exception:
                proc.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-lab', action='store_true',
                        help='Allow modification ONLY of the disposable fixture spawned by this script.')
    parser.add_argument('--output', type=Path, help='Optional JSON evidence file.')
    args = parser.parse_args()
    try:
        records = [once(args.write_lab), once(args.write_lab)]
        if records[0]['pid'] == records[1]['pid'] or records[0]['process_instance_id'] == records[1]['process_instance_id']:
            raise AssertionError('The process did not appear to restart independently.')
        result = {'passed': True, 'read_only': not args.write_lab,
                  'verified_on_two_lab_processes': True,
                  'universally_stable_pointer': False,
                  'source': 'offline_lab_instrumentation',
                  'note': 'Fresh addresses come from the owned fixture. This does not find a module-relative anchor in arbitrary games.',
                  'observations': records}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print('FAIL:', exc, file=sys.stderr)
        print('macOS may reject external task_for_pid; do not disable SIP or bypass target protections.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
