import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def test_real_fixture_exposes_layout_and_changes_own_values():
    proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'examples/offline_memory_lab.py')],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        details=json.loads(proc.stdout.readline())
        assert details['pid']==proc.pid
        assert int(details['health_address'],16)>0
        assert int(details['health_pointer_slot'],16)>0
        assert details['entity_layout']['count']==4
        assert details['entity_layout']['stride']==24
        for command, expected in [('damage 13', 87), ('heal 7', 94)]:
            proc.stdin.write(command+'\n');proc.stdin.flush()
            assert json.loads(proc.stdout.readline())['health']==expected
        proc.stdin.write('quit\n');proc.stdin.flush()
        assert json.loads(proc.stdout.readline())['stopped'] is True
        assert proc.wait(timeout=3)==0
    finally:
        if proc.poll() is None:
            proc.terminate();proc.wait(timeout=3)
