"""Read-only mapping recipes, bounded scans, and process identity tests."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('mapping_mcp', Path(__file__).resolve().parents[2] / 'src-tauri/resources/mac_memory_mcp.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
CHECK = 'mapping_check_' + 'a' * 32
CONTEXT = {'pid':42,'process_instance_id':'42:instance','program':'fixture.bin','build_sha256':'a'*64,'build_identity':'executable_file_sha256'}

class Backend:
    def __init__(self): self.data = bytearray(512); self.reads = []; self.writes = 0
    def regions(self): return [(0x1000,len(self.data),3)]
    def read(self, address, size):
        self.reads.append((address,size))
        if address < 0x1000 or address + size > 0x1000+len(self.data): raise m.MemError('read outside fixture')
        return bytes(self.data[address-0x1000:address-0x1000+size])
    def write(self,*_args): self.writes += 1; raise AssertionError('mapping wrote target memory')
    def close(self): pass
    def value(self, number): struct.pack_into('<i',self.data,32,number)

class MappingTests(unittest.TestCase):
    def setUp(self):
        self.old = m.S
        m.S = m.Session(); m.S.backend = self.backend = Backend(); m.S.pid = 42
        self.context = patch.object(m,'mapping_process_context',return_value=dict(CONTEXT)); self.context.start()
        self.backend.value(100)
    def tearDown(self): self.context.stop(); m.S = self.old
    def args(self, **changes):
        return {'check_id':CHECK,'kind':'address','base':'0x1020','value_type':'i32','pid':42,
                'process_instance_id':'42:instance','expected_value':'100',**changes}
    def check(self, **changes): return json.loads(m.t_verify_mapping(self.args(**changes)))
    def test_real_typed_readback_and_mismatch_without_writes(self):
        result=self.check()
        self.assertTrue(result['read_only']); self.assertTrue(result['samples'][0]['matched'])
        self.assertEqual(result['samples'][0]['value'],100)
        self.backend.value(90)
        self.assertFalse(self.check()['samples'][0]['matched'])
        self.assertEqual(self.backend.writes,0)
    def test_same_pid_new_instance_is_rejected_before_read(self):
        with self.assertRaisesRegex(m.MemError,'differs'): self.check(process_instance_id='42:restarted')
        self.assertEqual(self.backend.reads,[])
    def test_process_changes_during_read(self):
        with patch.object(m,'mapping_process_context',side_effect=[dict(CONTEXT),{**CONTEXT,'build_sha256':'b'*64}]):
            with self.assertRaisesRegex(m.MemError,'changed'): self.check()
    def test_scan_narrows_in_independent_state(self):
        m.S.scan = {'unrelated':'raw tool scan'}
        result=self.check(kind='value_scan',scan_id=None)
        self.assertEqual(result['candidate_count'],1)
        self.backend.value(90)
        result=self.check(kind='value_scan',scan_id=CHECK,expected_value='90',check_id='mapping_check_'+'b'*32)
        self.assertEqual(result['scan_id'],CHECK)
        self.assertEqual(result['samples'][0]['value'],90)
        self.assertEqual(m.S.scan,{'unrelated':'raw tool scan'})
        m.S.detach(); self.assertEqual(m.S.mapping_scans,{})
    def test_scan_byte_cap_and_expired_scan(self):
        with patch.object(m,'K_MAPPING_BYTES',64):
            self.assertTrue(self.check(kind='value_scan',scan_id=None)['partial'])
            self.assertLessEqual(sum(size for _,size in self.backend.reads),68)
        with self.assertRaisesRegex(m.MemError,'expired'): self.check(kind='value_scan',scan_id='missing')
    def test_module_recipe_resolves_pointer_and_readbacks(self):
        struct.pack_into('<Q',self.backend.data,8,0x1010)
        with patch.object(m,'get_loaded_modules',return_value=[{'name':'fixture.bin','base':0x1000}]):
            result=self.check(kind='module_pointer',module_name='fixture.bin',rva='0x8',offsets=['0x10'])
        self.assertEqual(result['samples'][0]['address'],'0x1020')
        self.assertTrue(result['samples'][0]['matched'])
    def test_ambiguous_module_and_invalid_numeric_fail_closed(self):
        with patch.object(m,'get_loaded_modules',return_value=[{'name':'fixture.bin','base':0x1000}]*2):
            with self.assertRaisesRegex(m.MemError,'ambiguous'): self.check(kind='module_pointer',module_name='fixture.bin',rva='0x8')
        for value in ('nan','inf','-inf'):
            with self.assertRaises(ValueError): self.check(value_type='f64',expected_value=value)
        with self.assertRaises(ValueError): self.check(value_type='i8',expected_value='128')
    def test_short_read_is_not_zero(self):
        with patch.object(self.backend,'read',return_value=b''):
            with self.assertRaisesRegex(m.MemError,'Short'): self.check()
    def test_no_scan_commit_after_identity_change(self):
        with patch.object(m,'mapping_process_context',side_effect=[dict(CONTEXT),{**CONTEXT,'pid':43}]):
            with self.assertRaises(m.MemError): self.check(kind='value_scan',scan_id=None)
        self.assertFalse(m.S.mapping_scans)

class NativeOwnedValueTest(unittest.TestCase):
    def test_native_mapping_reads_only_owned_memory(self):
        old = m.S
        backend=m.make_backend()
        try:
            backend.open(os.getpid())
            m.S=m.Session(); m.S.pid=os.getpid(); m.S.backend=backend
            value=ctypes.c_int32(127136871)
            context=m.mapping_process_context()
            result=json.loads(m.t_verify_mapping({'check_id':CHECK,'kind':'address','base':hex(ctypes.addressof(value)),
                'value_type':'i32','pid':context['pid'],'process_instance_id':context['process_instance_id'],'expected_value':str(value.value)}))
            self.assertTrue(result['samples'][0]['matched'])
            self.assertEqual(len(result['build_sha256']),64)
            self.assertEqual(value.value,127136871)
        finally:
            backend.close(); m.S=old

class MappingSelectionTest(unittest.TestCase):
    def test_process_choices_include_stable_creation_identity(self):
        from types import SimpleNamespace
        listing=' 42 Wed Nov  4 10:20:30 2026 /Applications/Fixture App\n 43 Wed Nov  4 10:20:30 2026 /bin/alpha\ninvalid\n'
        with patch.object(m.sys,'platform','darwin'), patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=listing)) as run:
            rows=json.loads(m.t_list_mapping_processes({}))['processes']
        self.assertEqual([r['program'] for r in rows],['alpha','Fixture App'])
        self.assertEqual(rows[1]['process_instance_id'],'42:mac:Wed Nov 4 10:20:30 2026')
        self.assertEqual(run.call_args.kwargs['env']['LC_ALL'],'C')

    def test_stale_selection_cannot_attach(self):
        with patch.object(m,'process_instance_identity',return_value='new'), patch.object(m.S,'attach') as attach:
            with self.assertRaisesRegex(m.MemError,'restarted'):
                m.t_attach_mapping_process({'pid':42,'process_instance_id':'old'})
            attach.assert_not_called()

    def test_selection_is_checked_again_after_attach(self):
        with patch.object(m,'process_instance_identity',return_value='old'), patch.object(m.S,'attach'), patch.object(m.S,'detach') as detach, patch.object(m,'mapping_process_context',return_value={**CONTEXT,'process_instance_id':'new'}):
            with self.assertRaisesRegex(m.MemError,'changed'):
                m.t_attach_mapping_process({'pid':42,'process_instance_id':'old'})
            detach.assert_called_once()

    def test_exact_selection_returns_native_fingerprint(self):
        with patch.object(m,'process_instance_identity',return_value=CONTEXT['process_instance_id']), patch.object(m.S,'attach') as attach, patch.object(m,'mapping_process_context',return_value=CONTEXT):
            self.assertEqual(json.loads(m.t_attach_mapping_process({'pid':42,'process_instance_id':CONTEXT['process_instance_id']})),CONTEXT)
            attach.assert_called_once_with(42)

class ExecutablePathTests(unittest.TestCase):
    def test_darwin_uses_kernel_path_when_process_name_is_relative(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        def native_path(pid, buffer, size):
            self.assertEqual(pid,42)
            self.assertEqual(size,4096)
            path=b'/Applications/Fixture App/fixture'
            buffer.value=path
            return len(path)
        call=Mock(side_effect=native_path)
        with patch.object(m.sys,'platform','darwin'), patch.object(m.ctypes,'CDLL',return_value=SimpleNamespace(proc_pidpath=call)), patch.object(m.subprocess,'run') as ps:
            self.assertEqual(m.process_executable_path(42),'/Applications/Fixture App/fixture')
            ps.assert_not_called()
        self.assertEqual(call.argtypes,[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32])
        self.assertEqual(call.restype,ctypes.c_int)

    def test_failed_or_truncated_path_has_no_name_fallback(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for length in (0,-1,4096):
            with patch.object(m.sys,'platform','darwin'), patch.object(m.ctypes,'CDLL',return_value=SimpleNamespace(proc_pidpath=Mock(return_value=length))), patch.object(m.subprocess,'run') as ps:
                with self.assertRaisesRegex(m.MemError,'resolve'): m.process_executable_path(42)
                ps.assert_not_called()

    def test_invalid_pid_is_rejected_before_loading_native_library(self):
        with patch.object(m.ctypes,'CDLL') as library:
            for pid in (True,0,1,-1,2**31,'42'):
                with self.assertRaises(ValueError): m.process_executable_path(pid)
            library.assert_not_called()
