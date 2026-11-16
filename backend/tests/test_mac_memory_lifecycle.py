"""Exercise the legacy facade against the adapter interface it owns."""
import struct
import threading
import pytest
from bughunter import mac_memory as module
from bughunter.memory_engine.protocol import MemoryEngineError


@pytest.fixture
def native(monkeypatch):
    instances = []
    class Adapter:
        def __init__(self, pid):
            self.pid, self.closed, self.writes = pid, 0, 0
            self.fail = False
            self.data = bytearray(struct.pack('<iiii', 99, 10, 99, 20))
            self.entered = self.release = None
            instances.append(self)
        def write_value(self, address, value, vtype='int', verify=True):
            self.writes += 1
            if self.fail:
                raise MemoryEngineError('write refused')
            if self.entered is not None:
                self.entered.set()
                assert self.release.wait(2)
            self.data[address-0x1000:address-0x1000+4] = struct.pack('<i', value)
        def read_bytes(self, address, size):
            return bytes(self.data[address-0x1000:address-0x1000+size])
        def read_value(self, address, vtype='int'):
            return struct.unpack('<i', self.read_bytes(address, 4))[0]
        def regions(self, writable_only=False):
            assert writable_only
            return [(0x1000, len(self.data))]
        def modules(self):
            return [{'path':'/fixture', 'base':'0x1000'}]
        def close(self):
            self.closed += 1
    monkeypatch.setattr(module, 'MacMemoryAdapter', Adapter)
    engine = module.MacMemory()
    engine.available = True
    monkeypatch.setattr(engine, 'list_processes', lambda *_: [])
    yield engine, instances
    engine.shutdown()


def test_refused_write_never_retries(native):
    engine, adapters = native
    engine.attach(42)
    adapters[0].fail = True
    with pytest.raises(module.MemoryError_, match='write refused'):
        engine.write_value(42, 0x1000, 99)
    assert adapters[0].writes == 1


def test_refused_initial_freeze_is_not_reported_active(native):
    engine, adapters = native
    engine.attach(42)
    adapters[0].fail = True
    with pytest.raises(module.MemoryError_, match='Cannot freeze'):
        engine.freeze(42, 0x1000, 99)
    assert engine._sessions[42].freezes == {}
    assert adapters[0].writes == 1


def test_failed_process_discovery_allocates_no_adapter(native, monkeypatch):
    engine, adapters = native
    def fail(*_):
        raise RuntimeError('process listing failed')
    monkeypatch.setattr(engine, 'list_processes', fail)
    with pytest.raises(RuntimeError, match='listing failed'):
        engine.attach(42)
    assert adapters == [] and engine._sessions == {}


def test_reattach_detach_and_shutdown_release_each_owner_once(native):
    engine, adapters = native
    engine.attach(42)
    stop = threading.Event()
    engine._sessions[42].freezes[0x1000] = stop
    engine.attach(42)
    assert stop.is_set()
    engine.attach(43)
    engine.detach(42)
    engine.detach(42)
    engine.shutdown()
    engine.shutdown()
    assert [a.closed for a in adapters] == [1, 1, 1]


def test_scan_narrow_results_and_module_forwarding(native):
    engine, adapters = native
    engine.attach(42)
    assert engine.scan(42, value=99)['matches'] == 2
    engine.write_value(42, 0x1008, 100)
    assert engine.next_scan(42, 'increased')['matches'] == 1
    assert engine.results(42)['results'] == [{'address':'0x1008', 'value':100}]
    assert engine.modules(42, 'fixture')['count'] == 1


def test_detach_waits_for_active_freeze_write(native):
    engine, adapters = native
    engine.attach(42)
    adapter = adapters[0]
    engine.freeze(42, 0x1000, 99)
    adapter.entered, adapter.release = threading.Event(), threading.Event()
    assert adapter.entered.wait(2)
    detached = threading.Event()
    def detach():
        engine.detach(42)
        detached.set()
    worker = threading.Thread(target=detach)
    worker.start()
    try:
        assert not detached.wait(.05)
        assert adapter.closed == 0
    finally:
        adapter.release.set()
        worker.join(2)
    assert detached.is_set() and adapter.closed == 1
    workers = [t for t in threading.enumerate() if t.name == "freeze-42-1000"]
    for thread in workers:
        thread.join(1)
    assert all(not thread.is_alive() for thread in workers)
    assert adapter.writes == 2
