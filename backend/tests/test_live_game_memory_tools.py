"""Actual read-only memory logic tested against a synthetic process address space."""
import importlib.util
import json
import struct
from pathlib import Path
import pytest

PATH = Path(__file__).resolve().parents[2] / 'src-tauri/resources/mac_memory_mcp.py'
spec = importlib.util.spec_from_file_location('live_game_memory_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Memory:
    def __init__(self):
        self.start = 0x3000
        self.data = bytearray(512)
        self.ptr = self.start + 8
        self.health = self.start + 100
        struct.pack_into('<Q', self.data, 8, self.health - 8)
        struct.pack_into('<i', self.data, 100, 100)
        self.entity_base = self.start + 200
        for index in range(3):
            struct.pack_into('<ffiiii', self.data, 200+index*24,
                             10.0+index, -2.0*index, 99-index, index%2+1, 1, index+1)

    def read(self, addr, n):
        offset = addr - self.start
        if offset < 0 or offset+n > len(self.data):
            raise bridge.MemError('invalid address')
        return bytes(self.data[offset:offset+n])


@pytest.fixture
def memory():
    original = bridge.S.backend, bridge.S.pid
    mem = Memory()
    bridge.S.backend, bridge.S.pid = mem, 12345
    yield mem
    bridge.S.backend, bridge.S.pid = original


def test_pointer_chain_resolves_each_time(memory):
    resolved, trail = bridge.resolve_chain(memory, memory.ptr, [8])
    assert resolved == memory.health
    assert trail[0]['offset'] == 8
    struct.pack_into('<Q', memory.data, 8, memory.health - 12)
    assert bridge.resolve_chain(memory, memory.ptr, [12])[0] == memory.health


def test_bounded_live_watch_with_pointer_trail(memory, monkeypatch):
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    data = json.loads(bridge.t_watch_value({'base':hex(memory.ptr), 'offsets':[8],
                                            'type':'i32','samples':3,'interval_s':.1}))
    assert [s['value'] for s in data['samples']] == [100,100,100]
    assert data['read_only'] and data['restart_stability'] == 'not_established'
    assert data['pid'] == 12345


def test_live_watch_rejects_unbounded_or_invalid_arguments(memory):
    for args in ({'samples': 41}, {'samples': 20, 'interval_s': 1},
                 {'samples': 2, 'interval_s': 0.001}, {'offsets': [9000]}):
        with pytest.raises((ValueError, bridge.MemError)):
            bridge.t_watch_value({'base': hex(memory.ptr), **args})


def test_entity_reader_does_not_guess_offsets(memory):
    data = json.loads(bridge.t_read_entity_layout({'base': hex(memory.entity_base),
        'count': 3, 'stride': 24, 'fields': [
            {'name':'x','type':'f32','offset':0},
            {'name':'y','type':'f32','offset':4},
            {'name':'health','type':'i32','offset':8},
            {'name':'team','type':'i32','offset':12}]}))
    assert len(data['entities']) == 3
    assert data['entities'][2]['x'] == 12.0
    assert data['entities'][1]['health'] == 98
    assert data['source'] == 'operator_supplied_layout'


def test_entity_reader_rejects_bad_layout(memory):
    base = hex(memory.entity_base)
    for fields, count, stride in [([{'name':'secret','type':'i32','offset':0}], 1,24),
                                   ([{'name':'health','type':'i32','offset':500}],1,24),
                                   ([{'name':'x','type':'i32','offset':0}]*2,1,24),
                                   ([{'name':'x','type':'f32','offset':0}],100,24),
                                   ([{'name':'x','type':'f32','offset':0}],1,600)]:
        with pytest.raises(ValueError):
            bridge.t_read_entity_layout({'base':base,'count':count,'stride':stride,'fields':fields})


def test_tool_schema_available_for_model(memory):
    tools = {row['name']: row for row in bridge.tool_list()}
    assert {'watch_value','read_entity_layout','find_pointer_references','verify_pointer_chain','change_unique_scan_result'} <= set(tools)
    assert 'base' in tools['watch_value']['inputSchema']['required']
