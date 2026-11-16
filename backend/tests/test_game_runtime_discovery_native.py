"""P05 dispatcher semantics and actual external-process discovery acceptance."""
from __future__ import annotations

import json
import struct
import sys
import threading
import time

import pytest

from bughunter.game_runtime.scans import ScanSession
from bughunter.memory_engine.ownership import ExecutorRegistry
from bughunter.memory_engine.protocol import MemoryEngineError, MemoryEngineUnavailable, ProcessIdentity
from bughunter.memory_engine.server import MemoryEngineServer, ServiceConfig


class FakeAdapter:
    def __init__(self, size=4096):
        self.base = 0x100000
        self.memory = bytearray(size)
        self.identity = ProcessIdentity(123, 987654, "a" * 64)
        self.closed = False
        self.reads = []

    def regions_writable(self):
        return [(self.base, len(self.memory))]

    def modules(self):
        return [{"base": hex(self.base), "size": len(self.memory), "path": "/fixture/FixtureModule"}]

    def read_bytes(self, address, size):
        self.reads.append((address, size))
        offset = address - self.base
        if not 0 <= offset <= len(self.memory) or offset + size > len(self.memory):
            raise RuntimeError("unmapped fixture address")
        return bytes(self.memory[offset:offset + size])

    def put(self, offset, fmt, value):
        struct.pack_into(fmt, self.memory, offset, value)

    def close(self):
        self.closed = True


@pytest.fixture
def memory():
    adapter = FakeAdapter()
    server = MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
    attached = server.attach(adapter, session_id="executor", mutating=False)
    params = {"session_id": "executor", "identity": attached["identity"], "task_session_id": "subscriber-a"}
    yield adapter, server, params
    server.shutdown()


def test_exact_scan_pages_and_subscriber_state_are_isolated(memory):
    adapter, server, params = memory
    adapter.memory[:250] = bytes([42]) * 250
    result = server.dispatch("memory.scan", {**params, "scan_id": "same", "value_type": "u8", "needle": 42})
    assert result["complete"] and result["total_matches"] == 250
    assert len(result["results"]) == 100 and result["next_cursor"]
    json.dumps(result, allow_nan=False)
    second = server.dispatch("memory.scan", {**params, "scan_mode": "results", "scan_id": "same",
                                           "cursor": result["next_cursor"], "max_results": 200})
    assert len(second["results"]) == 150 and second["next_cursor"] is None
    assert second["results"][0]["address"] == hex(adapter.base + 100)
    with pytest.raises(MemoryEngineError, match="subscriber"):
        server.dispatch("memory.scan", {**params, "task_session_id": "subscriber-b", "scan_id": "same", "scan_mode": "results"})
    other = server.dispatch("memory.scan", {**params, "task_session_id": "subscriber-b", "scan_id": "same",
                                          "value_type": "u8", "needle": 255})
    assert other["total_matches"] == 0
    server.dispatch("memory.scan", {**params, "scan_id": "same", "scan_mode": "close"})
    assert server.dispatch("memory.scan", {**params, "task_session_id": "subscriber-b", "scan_id": "same", "scan_mode": "results"})["complete"]


def test_unknown_refinement_and_stale_cursor(memory):
    adapter, server, params = memory
    adapter.memory[:250] = bytes([42]) * 250
    first = server.dispatch("memory.scan", {**params, "scan_id": "unknown", "scan_mode": "unknown_initial", "value_type": "u8"})
    assert first["snapshot_bytes"] == len(adapter.memory) and first["total_matches"] == 0
    adapter.memory[:250] = bytes([44]) * 250
    changed = server.dispatch("memory.scan", {**params, "scan_id": "unknown", "scan_mode": "refine", "refinement": "increased"})
    assert changed["total_matches"] == 250
    adapter.memory[1] = 45
    refined = server.dispatch("memory.scan", {**params, "scan_id": "unknown", "scan_mode": "refine", "refinement": "exact", "needle": 45})
    assert refined["results"] == [{"address": hex(adapter.base + 1), "value": 45, "value_type": "u8"}]
    with pytest.raises(MemoryEngineError, match="stale"):
        server.dispatch("memory.scan", {**params, "scan_id": "unknown", "scan_mode": "results", "cursor": changed["next_cursor"]})


def test_range_byte_pattern_and_partial_read(memory):
    adapter, server, params = memory
    adapter.put(40, "<I", 555)
    ranged = server.dispatch("memory.scan", {**params, "scan_mode": "range", "value_type": "u32", "low": 555, "high": 555})
    assert hex(adapter.base + 40) in [row["address"] for row in ranged["results"]]
    adapter.memory[100:105] = b"hello"
    pattern = server.dispatch("memory.scan", {**params, "scan_mode": "byte_pattern", "value_type": "bytes", "byte_pattern_hex": b"hello".hex()})
    assert pattern["results"] == [{"address": hex(adapter.base + 100), "value": b"hello".hex(), "value_type": "bytes"}]
    partial = server.dispatch("memory.scan", {**params, "value_type": "u32", "needle": 42,
                                               "regions": [{"base": "0x900000", "size": 4096}]})
    assert partial["partial"] and partial["progress"]["regions_skipped"] == 1


def test_scan_budgets_deadline_and_wire_validation(memory):
    adapter, server, params = memory
    limited = server.dispatch("memory.scan", {**params, "value_type": "u8", "needle": 42, "max_scan_bytes": 32})
    assert limited["partial"] and limited["status"] == "budget_exhausted"
    assert limited["progress"]["bytes_read"] == 32
    deadline = server.dispatch("memory.scan", {**params, "value_type": "u8", "needle": 42, "deadline_s": 1e-9})
    assert deadline["partial"] and deadline["status"] == "time_budget_exhausted"
    for changed in ({"max_results": 201}, {"deadline_s": 21}, {"task_session_id": ""}, {"regions": [{"base": 123, "size": 4}]}):
        with pytest.raises(MemoryEngineError):
            server.dispatch("memory.scan", {**params, "value_type": "u8", "needle": 42, **changed})


def test_cancel_does_not_wait_for_dispatch_lock(memory):
    adapter, server, params = memory
    began, release = threading.Event(), threading.Event()
    original_read = adapter.read_bytes
    def blocked_read(address, size):
        began.set()
        assert release.wait(2)
        return original_read(address, size)
    adapter.read_bytes = blocked_read
    results, errors = [], []
    def work():
        try:
            results.append(server.dispatch("memory.scan", {**params, "scan_id": "cancel-me", "value_type": "u8", "needle": 0}))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert began.wait(1)
        start = time.monotonic()
        assert server.cancel_work(task_session_id="another-subscriber")["cancelled_work"] == 0
        assert server.cancel_work(task_session_id=params["task_session_id"])["cancelled_work"] == 1
        assert time.monotonic() - start < .2
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not errors
    assert results[0]["status"] == "cancelled" and results[0]["partial"]


def test_pointer_paths_and_known_build_diagnostic_candidates(memory, monkeypatch):
    from bughunter.game_runtime.among_us import SUPPORTED_BUILD_FINGERPRINT, KILL_TIMER_INSTANCE_OFFSET
    adapter, server, params = memory
    target = adapter.base + 0x500
    adapter.put(0x40, "<Q", target)
    paths = server.dispatch("memory.pointer_search", {**params, "target_address": hex(target), "depth": 1, "offset_window": 0})
    assert paths["complete"]
    assert any(row["root"] == "mod:FixtureModule+0x40" for row in paths["results"])
    from bughunter.game_runtime import live_target
    monkeypatch.setattr(live_target, "process_image", lambda pid: (None, "a" * 64, adapter.identity.start_time_ns))
    with pytest.raises(MemoryEngineError, match="fingerprint"):
        server.dispatch("memory.pointer_search", {**params, "search_mode": "among_us_candidates"})
    # Runtime task identities use a composite fingerprint; qualification is
    # based on the independently measured executable digest and process birth.
    monkeypatch.setattr(live_target, "process_image", lambda pid: (None, SUPPORTED_BUILD_FINGERPRINT, adapter.identity.start_time_ns))
    with pytest.raises(MemoryEngineError, match="qualified image"):
        server.dispatch("memory.pointer_search", {**params, "search_mode": "among_us_candidates", "qualified_image_fingerprint": "b" * 64})
    adapter.put(0x100, "<Q", adapter.base + 0x600)
    adapter.put(0x600 + KILL_TIMER_INSTANCE_OFFSET, "<f", 27.5)
    candidates = server.dispatch("memory.pointer_search", {**params, "search_mode": "among_us_candidates"})
    assert any(row["address"] == hex(adapter.base + 0x600) and row["kill_timer"] == 27.5 for row in candidates["results"])
    assert all(row["verification_status"] == "candidate" for row in candidates["results"])


def test_pointer_search_byte_budget_reports_partial(memory):
    adapter, server, params = memory
    result = server.dispatch("memory.pointer_search", {**params, "target_address": hex(adapter.base + 0x100), "max_scan_bytes": 32})
    assert result["partial"] and result["reason"] == "budget_exhausted"


def test_measured_module_range_and_refinement_keep_incomplete_coverage(memory):
    adapter, server, params = memory
    adapter.put(0x40, "<Q", adapter.base + 0x100)
    # Native module inspection may expose only bases. The fixture supplies
    # measured image extents through the service instead.
    adapter.modules = lambda: [{"base": hex(adapter.base), "path": "/fixture/base-only"}]
    paths = server.dispatch("memory.pointer_search", {**params, "target_address": hex(adapter.base + 0x100),
        "depth": 1, "offset_window": 0, "module_ranges": [{"name": "MeasuredFixture", "base": hex(adapter.base), "size": 4096}]})
    assert paths["results"][0]["root"] == "mod:MeasuredFixture+0x40"
    initial = server.dispatch("memory.scan", {**params, "scan_id": "partial-baseline", "scan_mode": "unknown_initial",
                                               "value_type": "u8", "max_scan_bytes": 32})
    assert initial["partial"]
    adapter.memory[3] = 100
    changed = server.dispatch("memory.scan", {**params, "scan_id": "partial-baseline", "scan_mode": "refine", "refinement": "changed"})
    assert changed["partial"] and not changed["complete"] and changed["reason"] == "budget_exhausted"
    assert changed["results"] == [{"address": hex(adapter.base + 3), "value": 100, "value_type": "u8"}]


def test_checkpoint_restores_real_unknown_baseline_and_rejects_new_birth(tmp_path):
    adapter = FakeAdapter(512)
    scan = ScanSession(adapter, "u32")
    scan.scan_unknown_initial()
    manifest = scan.export_checkpoint(tmp_path / "saved")
    scan.close()
    adapter.put(40, "<I", 112233)
    restored = ScanSession.restore_checkpoint(adapter, manifest.parent)
    try:
        assert adapter.base + 40 in {row.address for row in restored.refine("changed")}
        next_manifest = restored.export_checkpoint(tmp_path / "saved-again")
        resumed = ScanSession.restore_checkpoint(adapter, next_manifest.parent)
        try:
            assert adapter.base + 40 in {row.address for row in resumed.refine("exact", needle=112233)}
        finally:
            resumed.close()
    finally:
        restored.close()
    adapter.identity = ProcessIdentity(123, 987655, "a" * 64)
    with pytest.raises(ValueError, match="identity"):
        ScanSession.restore_checkpoint(adapter, manifest.parent)


def test_checkpoint_rejects_tampered_snapshot_and_retains_partial_coverage(tmp_path):
    adapter = FakeAdapter(512)
    scan = ScanSession(adapter, "u8")
    scan.set_limits(max_read_bytes=32)
    from bughunter.game_runtime.scans import ScanBudgetExhausted
    with pytest.raises(ScanBudgetExhausted):
        scan.scan_unknown_initial()
    manifest = scan.export_checkpoint(tmp_path / "partial")
    restored = ScanSession.restore_checkpoint(adapter, manifest.parent)
    assert restored.progress.phase == "budget_exhausted" and restored._snapshot_bytes == 32
    restored.close()
    (manifest.parent / "snapshot-00000000.bin").write_bytes(bytes([42]) * 32)
    with pytest.raises(ValueError, match="digest"):
        ScanSession.restore_checkpoint(adapter, manifest.parent)
    scan.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="external macOS native adapter acceptance")
def test_native_scan_and_pointer_search_on_external_fixture():
    from bughunter.game_runtime.testing import Fixture, DEFAULT_LAYOUT
    from bughunter.memory_engine.mac_adapter import MacMemoryAdapter
    from bughunter.memory_engine.protocol import read_process_start_time_ns
    with Fixture() as fixture:
        observed = fixture.identity()
        identity = ProcessIdentity(observed.pid, read_process_start_time_ns(observed.pid), observed.module_fingerprint)
        try:
            adapter = MacMemoryAdapter(observed.pid, identity=identity)
        except MemoryEngineUnavailable as exc:
            pytest.skip(str(exc))
        server = MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
        server.attach(adapter, session_id="native", mutating=False)
        params = {"session_id": "native", "task_session_id": "native-task", "identity": identity.to_wire(),
                  "regions": [{"base": hex(row.base), "size": row.size} for row in observed.region_map]}
        try:
            spec = DEFAULT_LAYOUT.field("countdown")
            address = observed.region(spec.region).base + spec.offset
            first = server.dispatch("memory.scan", {**params, "scan_id": "timer", "value_type": "u32", "needle": 60})
            assert first["complete"] and hex(address) in {row["address"] for row in first["results"]}
            fixture.set("countdown", 71)
            changed = server.dispatch("memory.scan", {**params, "scan_id": "timer", "scan_mode": "refine", "refinement": "exact", "needle": 71})
            assert hex(address) in {row["address"] for row in changed["results"]}
            target = observed.region("region_1").base + 0x200
            paths = server.dispatch("memory.pointer_search", {**params, "target_address": hex(target), "depth": 1, "offset_window": 0})
            assert paths["complete"] and paths["results"]
        finally:
            server.shutdown()
