"""Native engine ownership and refused writes, with isolated Mach API doubles."""
import ctypes
import importlib.util
from pathlib import Path
import threading
from unittest.mock import patch

import pytest


class Function:
    def __init__(self, implementation=lambda *args: 0):
        self.implementation = implementation
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.implementation(*args)


class Mach:
    def __init__(self):
        self.functions = {}
        self.task_for_pid.implementation = self.attach

    def __getattr__(self, name):
        return self.functions.setdefault(name, Function())

    @staticmethod
    def attach(owner, pid, result):
        ctypes.cast(result, ctypes.POINTER(ctypes.c_uint))[0] = pid + 100
        return 0


@pytest.fixture
def native(monkeypatch):
    library = Mach()
    path = Path(__file__).resolve().parents[1] / 'bughunter/mac_memory.py'
    spec = importlib.util.spec_from_file_location('mac_memory_lifecycle_fixture', path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(ctypes, 'CDLL', return_value=library):
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_mach_task_self', lambda: 10)
    engine = module.MacMemory()
    monkeypatch.setattr(engine, 'list_processes', lambda *_: [])
    yield module, engine, library
    engine.shutdown()


def test_refused_write_never_changes_protection_or_retries(native):
    module, engine, library = native
    engine.attach(42)
    library.mach_vm_write.implementation = lambda *_: 2
    with pytest.raises(module.MemoryError_, match='Page permissions were not changed'):
        engine.write_value(42, 0x1000, 99)
    assert len(library.mach_vm_write.calls) == 1
    assert 'mach_vm_protect' not in library.functions


def test_refused_initial_freeze_is_not_reported_active(native):
    module, engine, library = native
    engine.attach(42)
    library.mach_vm_write.implementation = lambda *_: 2
    with pytest.raises(module.MemoryError_, match='initial write failed'):
        engine.freeze(42, 0x1000, 99)
    assert engine._sessions[42].freezes == {}
    assert len(library.mach_vm_write.calls) == 1


def test_failed_process_discovery_releases_new_port(native, monkeypatch):
    _, engine, library = native
    def fail(*_):
        raise RuntimeError('process listing failed')
    monkeypatch.setattr(engine, 'list_processes', fail)
    with pytest.raises(RuntimeError, match='listing failed'):
        engine.attach(42)
    assert library.mach_port_deallocate.calls == [(10, 142)]
    assert engine._sessions == {}


def test_reattach_detach_and_shutdown_release_each_owned_reference_once(native):
    _, engine, library = native
    engine.attach(42)
    previous = engine._sessions[42]
    stop = threading.Event()
    previous.freezes[0x1000] = stop
    engine.attach(42)
    assert stop.is_set() and previous.task == 0
    engine.attach(43)
    engine.detach(42)
    engine.detach(42)
    engine.shutdown()
    engine.shutdown()
    assert library.mach_port_deallocate.calls == [(10, 142), (10, 142), (10, 143)]
    assert engine._sessions == {}


def test_region_enumeration_releases_returned_object_port(native):
    module, engine, library = native
    def region(task, address, size, flavor, info, count, object_name):
        if len(library.mach_vm_region.calls) > 1:
            return 1
        ctypes.cast(address, ctypes.POINTER(ctypes.c_ulonglong))[0] = 0x1000
        ctypes.cast(size, ctypes.POINTER(ctypes.c_ulonglong))[0] = 0x100
        ctypes.cast(info, ctypes.POINTER(module._BasicInfo64))[0].protection = 3
        ctypes.cast(object_name, ctypes.POINTER(ctypes.c_uint))[0] = 555
        return 0
    library.mach_vm_region.implementation = region
    assert list(engine._regions(142)) == [(0x1000, 0x100)]
    assert library.mach_port_deallocate.calls == [(10, 555)]


def test_detach_waits_for_active_freeze_write_and_stops_worker(native, monkeypatch):
    _, engine, library = native
    engine.attach(42)
    entered, release, detached = threading.Event(), threading.Event(), threading.Event()
    workers = []
    original_thread = threading.Thread
    def record_thread(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        workers.append(thread)
        return thread
    monkeypatch.setattr(threading, 'Thread', record_thread)
    def write(*_):
        if len(library.mach_vm_write.calls) == 2:
            entered.set()
            assert release.wait(2), 'test did not release the active write'
        return 0
    library.mach_vm_write.implementation = write
    engine.freeze(42, 0x1000, 99)
    assert entered.wait(2)
    def detach():
        engine.detach(42)
        detached.set()
    closer = original_thread(target=detach)
    closer.start()
    try:
        assert not detached.wait(.05)
        assert library.mach_port_deallocate.calls == []
    finally:
        release.set()
        closer.join(2)
        for thread in workers:
            thread.join(2)
    assert detached.is_set()
    assert all(not thread.is_alive() for thread in workers)
    assert len(library.mach_vm_write.calls) == 2
    assert library.mach_port_deallocate.calls == [(10, 142)]
