"""Module-relative pointer logic: never infer an unknown module address."""
import importlib.util
import json
import struct
from pathlib import Path
import pytest

PATH=Path(__file__).resolve().parents[2]/'src-tauri/resources/mac_memory_mcp.py'
spec=importlib.util.spec_from_file_location('pointer_module_test',PATH)
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


def test_linux_module_base_subtracts_file_offsets():
    text='''10001000-10002000 r--p 00001000 00:01 100 /tmp/test-bin
10002000-10003000 r-xp 00002000 00:01 100 /tmp/test-bin
20000000-20001000 rw-p 00000000 00:01 102 /tmp/other.so
'''
    modules=bridge._modules_from_linux_maps(text)
    assert any(m['base']==0x10000000 and m['name']=='test-bin' for m in modules)
    assert any(m['base']==0x20000000 and m['name']=='other.so' for m in modules)


def test_vmmap_parser_reports_only_observed_lines():
    text='''__TEXT  102300000-102320000 [128K] r-x/r-x SM=COW /Applications/Owned.app/Contents/MacOS/Owned
__DATA  102320000-102330000 [64K] rw-/rw- SM=COW /Applications/Owned.app/Contents/MacOS/Owned
synthetic warning: unknown path
'''
    modules=bridge._modules_from_vmmap(text)
    assert modules==[{'name':'Owned','base':0x102300000,'source':'vmmap_unverified_parser'}]
    assert bridge._modules_from_vmmap('no suitable lines')==[]


class MockMemory:
    def __init__(self):
        self.data=bytearray(256)
        struct.pack_into('<Q', self.data,32,0x5000+100-8)
        struct.pack_into('<i', self.data,100,77)
    def read(self,addr,size):
        offset=addr-0x5000
        if offset<0 or offset+size>len(self.data):raise bridge.MemError('bad address')
        return bytes(self.data[offset:offset+size])


def test_module_pointer_recipe_resolves_and_verifies(monkeypatch):
    before=bridge.S.backend,bridge.S.pid
    bridge.S.backend,bridge.S.pid=MockMemory(),12345
    monkeypatch.setattr(bridge,'get_loaded_modules',lambda pid:[{'name':'Owned','base':0x5000,'source':'mock'}])
    try:
        result=json.loads(bridge.t_verify_module_pointer_chain({'module_name':'Owned',
          'rva':'0x20','offsets':[8],'expected_value':77,'type':'i32'}))
        assert result['matched'] and result['resolved_address']=='0x5064'
        assert result['stable_across_restarts']=='not_established'
        with pytest.raises(bridge.MemError):
            bridge.t_verify_module_pointer_chain({'module_name':'Missing','rva':32,'expected_value':77})
        with pytest.raises(ValueError):
            bridge.t_verify_module_pointer_chain({'module_name':'Owned','rva':-1,'expected_value':77})
    finally:
        bridge.S.backend,bridge.S.pid=before


def test_module_tool_schemas_are_exposed():
    tools={tool['name'] for tool in bridge.tool_list()}
    assert {'list_loaded_modules','verify_module_pointer_chain'} <= tools
