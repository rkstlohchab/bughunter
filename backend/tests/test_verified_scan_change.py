"""Mock memory tests for single-candidate, typed, read-back-verified modifications."""
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'src-tauri/resources/mac_memory_mcp.py'
spec=importlib.util.spec_from_file_location('mac_memory_test_change',SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FakeMemory:
    def __init__(self, address=0x1000, value=100):
        self.addr=address
        self.data={address:m.encode('i32',value)}
        self.writes=0
    def read(self,addr,size):
        return self.data[addr][:size]
    def write(self,addr,data):
        self.data[addr]=data
        self.writes+=1


def session_with_scan(monkeypatch,candidates=None,value=100):
    fake=FakeMemory(value=value)
    sess=m.Session()
    sess.backend=fake
    sess.pid=12345
    sess.scan={'type':'i32','size':4,'values': candidates if candidates is not None else {fake.addr:fake.data[fake.addr]},'truncated':False}
    monkeypatch.setattr(m,'S',sess)
    return fake,sess


def test_verified_unique_change(monkeypatch):
    fake,_=session_with_scan(monkeypatch)
    result=m.t_change_unique_scan_result({'expected_value':100,'new_value':500})
    assert '"readback_matches": true' in result
    assert m.decode('i32',fake.read(0x1000,4))==500
    assert fake.writes==1


def test_rejects_ambiguous_or_truncated_scans(monkeypatch):
    fake,sess=session_with_scan(monkeypatch)
    sess.scan['values'][0x2000]=m.encode('i32',100)
    with pytest.raises(m.MemError,match='exactly one'):
        m.t_change_unique_scan_result({'expected_value':100,'new_value':500})
    sess.scan['values']={fake.addr:fake.data[fake.addr]}
    sess.scan['truncated']=True
    with pytest.raises(m.MemError,match='complete scan'):
        m.t_change_unique_scan_result({'expected_value':100,'new_value':500})
    assert fake.writes==0


def test_stale_scans_never_write(monkeypatch):
    fake,_=session_with_scan(monkeypatch)
    fake.data[fake.addr]=m.encode('i32',80)
    with pytest.raises(m.MemError,match='changed since'):
        m.t_change_unique_scan_result({'expected_value':100,'new_value':500})
    assert fake.writes==0


def test_wrong_expected_value_does_not_write(monkeypatch):
    fake,_=session_with_scan(monkeypatch)
    with pytest.raises(m.MemError,match='changed since'):
        m.t_change_unique_scan_result({'expected_value':99,'new_value':500})
    assert fake.writes==0
