"""Self-test must use the actual backend for an isolated, restored value."""
import ctypes
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'src-tauri/resources/mac_memory_mcp.py'
MODULE = importlib.util.module_from_spec(importlib.util.spec_from_file_location('bh_test_selftest', SCRIPT))
spec = MODULE.__spec__
spec.loader.exec_module(MODULE)


class MemoryAdapter:
    name = 'fake'
    def __init__(self, fail_write=False):
        self.opened = False
        self.closed = False
        self.fail_write = fail_write
        self.write_count = 0
    def open(self, pid):
        assert pid > 0
        self.opened = True
    def read(self, addr, size):
        assert self.opened and size == 8
        return ctypes.string_at(addr, size)
    def write(self, addr, data):
        if self.fail_write:
            raise MODULE.MemError('write denied by operating system')
        self.write_count += 1
        ctypes.memmove(addr, data, len(data))
    def close(self):
        self.closed = True


def test_selftest_isolated_success(monkeypatch):
    fake = MemoryAdapter()
    monkeypatch.setattr(MODULE, 'make_backend', lambda: fake)
    original_session = MODULE.S
    result = json.loads(MODULE.t_bridge_self_test({}))
    assert result['passed'] is True
    assert result['external_process_touched'] is False
    assert result['backend'] == 'fake'
    assert fake.write_count == 2 and fake.closed
    assert MODULE.S is original_session and MODULE.S.pid is None


def test_selftest_reports_failed_write_without_false_success(monkeypatch):
    fake = MemoryAdapter(fail_write=True)
    monkeypatch.setattr(MODULE, 'make_backend', lambda: fake)
    try:
        MODULE.t_bridge_self_test({})
        assert False, 'The native write should have been rejected'
    except MODULE.MemError as error:
        assert 'write denied' in str(error)
    assert fake.closed


def test_full_stdio_selftest():
    completed = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_memory_engine_live.py')],
                               capture_output=True, text=True, timeout=20)
    report = json.loads(completed.stdout)
    if sys.platform.startswith('linux'):
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert report['passed'] is True
        assert report['backend'] == 'procfs'
    else:
        # macOS entitlement behavior requires a live Mac; a failure must be explicit.
        assert report.get('passed') is True or report.get('error')


def test_mach_self_task_port_never_deallocated():
    """Test the Mac-specific port lifecycle without calling Apple's APIs on Linux."""
    fake = type('FakeLib', (), {'task_for_pid': lambda *args: (_ for _ in ()).throw(AssertionError('unexpected task_for_pid')),
                                'mach_port_deallocate': lambda *args: (_ for _ in ()).throw(AssertionError('unexpected deallocate'))})()
    mach = object.__new__(MODULE.MachBackend)
    mach.lib = fake
    mach.self_task = 123
    mach.task = None
    mach._owns_task_ref = False
    mach.open(__import__('os').getpid())
    assert mach.task == 123 and not mach._owns_task_ref
    mach.close()
    assert mach.task is None
