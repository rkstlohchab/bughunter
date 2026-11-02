"""Cross-platform smoke checks for Mac-only automation and isolated test fixture."""
from pathlib import Path
import subprocess
import sys
import json

ROOT=Path(__file__).resolve().parents[2]


def test_native_axbridge_includes_foreground_gate_and_no_shell():
    code=(ROOT/'src-tauri/resources/axbridge.swift').read_text()
    assert 'AXIsProcessTrusted()' in code
    assert 'apps.first(where: { $0.isActive })' in code
    assert 'AXUIElementPerformAction' in code
    assert 'protected(element)' in code
    assert 'Process(' not in code
    assert 'Process(' not in code


def test_mac_native_helper_is_connected_to_rust_without_model_shell():
    code=(ROOT/'src-tauri/src/mac_desktop.rs').read_text()
    assert 'fn native_ax(' in code
    assert 'if let Some(result)=native_ax(session,action,value)' in code
    assert 'value.len()>180' in code
    assert 'session.cancelled.load' in code
    assert 'Command::new(executable)' in code
    assert '.arg(&session.bundle_id).arg(action).arg(value)' in code


def test_offline_money_fixture_lifecycle():
    proc=subprocess.Popen([sys.executable, '-u',str(ROOT/'examples/offline_memory_lab.py')],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        intro=json.loads(proc.stdout.readline())
        assert intro['pid']==proc.pid
        assert intro['money']==13371337
        proc.stdin.write('earn 7\nstatus\nquit\n');proc.stdin.flush()
        assert json.loads(proc.stdout.readline())['money']==13371344
        assert json.loads(proc.stdout.readline())['money']==13371344
        assert json.loads(proc.stdout.readline())['stopped'] is True
    finally:
        proc.terminate()
        proc.wait(timeout=3)
