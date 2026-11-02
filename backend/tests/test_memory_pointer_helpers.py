"""Read-only pointer-candidate tests with a synthetic local address space."""
import importlib.util
import json
import struct
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / 'src-tauri' / 'resources' / 'mac_memory_mcp.py'
spec = importlib.util.spec_from_file_location('bughunter_test_memory_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

class FakeMemory:
    def __init__(self):
        self.start = 0x100000
        self.data = bytearray(1024)
        self.target = self.start + 400
        self.ptr = self.start + 32
        struct.pack_into('<Q', self.data, 32, self.target - 24)
        struct.pack_into('<i', self.data, 400, 100)

    def regions(self):
        return iter([(self.start, len(self.data), 3)])

    def read(self, addr, size):
        pos = addr - self.start
        if pos < 0 or pos + size > len(self.data):
            raise bridge.MemError('Bad address')
        return bytes(self.data[pos:pos+size])


def test_finds_bounded_single_level_pointer_candidates():
    memory = FakeMemory()
    hits, scanned, partial = bridge.find_pointer_references(memory, memory.target, max_offset=32, max_bytes=1024)
    assert {'pointer_address':hex(memory.ptr), 'pointee':hex(memory.target - 24), 'offset':24} in hits
    assert scanned <= 1024 and not partial


def test_rejects_oversized_or_invalid_pointer_scans():
    mem = FakeMemory()
    for bad in [-1, 5000]:
        with pytest.raises(ValueError): bridge.find_pointer_references(mem, mem.target, max_offset=bad)
    with pytest.raises(ValueError): bridge.find_pointer_references(mem, mem.target, max_bytes=64 * 1024 * 1024)
    with pytest.raises(ValueError): bridge.find_pointer_references(mem, mem.target, max_results=1000)


def test_chain_verified_only_in_current_process():
    original = bridge.S.backend, bridge.S.pid
    try:
        mem = FakeMemory()
        bridge.S.backend, bridge.S.pid = mem, 420
        result = json.loads(bridge.t_verify_pointer_chain({'base':hex(mem.ptr), 'offsets':[24],
                                                             'type':'i32', 'expected_value':100}))
        assert result['matched'] is True
        assert result['stable_across_restarts'] == 'not_established'
        bad = json.loads(bridge.t_verify_pointer_chain({'base':hex(mem.ptr), 'offsets':[24],
                                                         'type':'i32', 'expected_value':99}))
        assert bad['matched'] is False
    finally:
        bridge.S.backend, bridge.S.pid = original


def test_pointer_tools_registered_but_do_not_write_memory():
    names = {spec['name'] for spec in bridge.tool_list()}
    assert {'find_pointer_references', 'verify_pointer_chain'} <= names
    assert 'write_memory' in names  # Separate, approval-gated legacy tool
