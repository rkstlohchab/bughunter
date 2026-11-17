"""Live analysis: pure classification/dissect/diff and the helper's bounded analysis lane."""
from __future__ import annotations

import struct
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_memory import FakeMemory, il2cpp_image  # noqa: E402

from bughunter.game_runtime import live_analysis  # noqa: E402
from bughunter.memory_engine.analysis import AnalysisOperations  # noqa: E402
from bughunter.memory_engine.protocol import MemoryEngineError  # noqa: E402
from bughunter.memory_engine.server import MemoryEngineServer, ServiceConfig  # noqa: E402
from bughunter.memory_engine.ownership import ExecutorRegistry  # noqa: E402


def _window(timer, counter, flag, pointer, vec):
    return (struct.pack("<f", timer) + struct.pack("<i", counter) + struct.pack("<i", flag) + struct.pack("<i", 7)
            + struct.pack("<Q", pointer) + struct.pack("<fff", *vec))


def test_classify_labels_measured_behaviour():
    times = [i * 0.1 for i in range(11)]
    samples = []
    for i, t in enumerate(times):
        counter = 30 - (1 if i >= 3 else 0) - (1 if i >= 7 else 0)
        samples.append(_window(60.0 - 2.0 * t, counter, i % 2, 0x1000_0040, (1.5 + i, 2.5 + i, 3.5 + i)))
    result = live_analysis.classify(samples, times, 0x2000_0000, readable=[(0x1000_0000, 0x1000)],
                                    modules=[("GameAssembly.dylib", 0x1000_0000, 0x1000)])
    rows = {r["offset"]: r for r in result["rows"]}
    assert rows["0x0"]["label"] == "timer" and rows["0x0"]["type"] == "f32"
    assert rows["0x0"]["rate_per_s"] == pytest.approx(-2.0, abs=1e-3)
    assert rows["0x4"]["label"] == "counter" and rows["0x4"]["steps"] == [-1]
    assert rows["0x8"]["label"] == "flag"
    assert rows["0x10"]["type"] == "pointer" and rows["0x10"]["target"] == "GameAssembly.dylib+0x40"
    assert any(r["label"] == "vector3_candidate" and r["offset"] == "0x18" for r in result["rows"])
    assert result["constant_slots"] == 1 and result["achieved_hz"] == pytest.approx(10.0)


def test_classify_rejects_mismatched_samples():
    with pytest.raises(ValueError):
        live_analysis.classify([b"\0" * 8], [0.0], 0x1000)
    with pytest.raises(ValueError):
        live_analysis.classify([b"\0" * 8, b"\0" * 12], [0.0, 1.0], 0x1000)


def test_dissect_and_diff():
    raw = struct.pack("<Q", 0x1000_0010) + struct.pack("<ff", 100.0, -2.5) + b"Weapon\0\0"
    rows = live_analysis.dissect(raw, 0x2000_0000, readable=[(0x1000_0000, 0x100)],
                                 modules=[("UnityPlayer.dylib", 0x1000_0000, 0x100)])
    assert rows[0]["pointer"] == "UnityPlayer.dylib+0x10"
    assert rows[1]["f32"] == [100.0, -2.5]
    assert rows[2]["ascii"] == "Weapon"
    changed = live_analysis.diff(struct.pack("<ii", 30, 5), struct.pack("<ii", 29, 5), 0x3000)
    assert changed == [{"offset": "0x0", "address": "0x3000", "i32": [30, 29], "delta_i32": -1}]


# --------------------------------------------------------------- helper lane


@pytest.fixture
def lane():
    mem, at = il2cpp_image()
    server = MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
    attached = server.attach(mem, session_id="exec")
    assert attached["status"] == "attached"
    ops = AnalysisOperations(server)
    base = {"session_id": "exec", "identity": mem.identity.to_wire(), "task_session_id": "task-1"}
    yield ops, mem, at, base, server
    ops.close()
    server.shutdown()


def test_watch_samples_while_target_changes(lane):
    ops, mem, at, base, _ = lane
    obj = at["instances"][0]
    stop = threading.Event()

    def game():
        value = 30
        while not stop.is_set():
            time.sleep(0.12)
            value -= 1
            mem.i32(obj + 0x24, value)

    thread = threading.Thread(target=game)
    thread.start()
    try:
        result = ops.dispatch({**base, "analysis_action": "watch", "address": hex(obj), "size": 64,
                               "duration_s": 0.8, "interval_ms": 20})
    finally:
        stop.set()
        thread.join()
    assert result["complete"] and result["samples"] >= 10 and result["achieved_hz"] > 5
    row = next(r for r in result["windows"][0]["rows"] if r["offset"] == "0x24")
    assert row["type"] == "i32" and row["label"] in ("counter", "timer", "decreasing")
    assert row["first"] > row["last"]
    assert any(f["address"] == "abs:" + hex(obj + 0x24) for f in result["findings"])


def test_watch_cancel_and_partial_read(lane):
    ops, _, at, base, server = lane
    timer = threading.Timer(0.15, lambda: server.cancel_work(session_id="exec"))
    timer.start()
    result = ops.dispatch({**base, "analysis_action": "watch", "address": hex(at["instances"][0]), "size": 16,
                           "duration_s": 5, "interval_ms": 20})
    timer.join()
    assert result["reason"] == "cancelled" and not result["complete"]
    failed = ops.dispatch({**base, "analysis_action": "watch", "address": "0x7000000000", "size": 16, "duration_s": 0.2})
    assert not failed["complete"] and failed["reason"].startswith("read_failed")


def test_dissect_labels_il2cpp_objects(lane):
    ops, _, at, base, _ = lane
    result = ops.dispatch({**base, "analysis_action": "dissect", "address": hex(at["instances"][0]), "size": 64,
                           "il2cpp_name_slot": "0x10"})
    assert result["object_class"] == "PlayerWeapon"
    assert result["rows"][0]["u64"] == hex(at["weapon"])


def test_snapshot_diff_is_task_owned(lane):
    ops, mem, at, base, _ = lane
    obj = at["instances"][1]
    snap = ops.dispatch({**base, "analysis_action": "snapshot", "address": hex(obj), "size": 64})
    mem.i32(obj + 0x24, 11)
    diff = ops.dispatch({**base, "analysis_action": "diff", "snapshot_id": snap["snapshot_id"]})
    assert diff["changed"] == 1 and diff["rows"][0]["offset"] == "0x24" and diff["rows"][0]["i32"] == [29, 11]
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "task_session_id": "task-2", "analysis_action": "diff", "snapshot_id": snap["snapshot_id"]})
    ops.release_task("task-1")
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "analysis_action": "diff", "snapshot_id": snap["snapshot_id"]})


def test_probe_continues_by_job_id_and_reports_findings_once(lane):
    ops, _, at, base, _ = lane
    first = ops.dispatch({**base, "analysis_action": "probe", "probe": "il2cpp_class", "name": "PlayerWeapon",
                          "max_scan_bytes": 0x5000})
    assert not first["complete"] and first["reason"] == "budget_exhausted" and first["findings"] == []
    result = first
    for _ in range(100):
        if result["complete"]:
            break
        result = ops.dispatch({**base, "analysis_action": "probe", "job_id": first["job_id"], "max_scan_bytes": 0x5000})
    assert result["complete"] and result["result"]["candidates"][0]["class_address"] == hex(at["weapon"])
    assert [f["kind"] for f in result["findings"]] == ["class_layout"]
    with pytest.raises(MemoryEngineError):  # finished jobs are released
        ops.dispatch({**base, "analysis_action": "probe", "job_id": first["job_id"]})


def test_lane_rejects_bad_requests(lane):
    ops, _, _, base, _ = lane
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "analysis_action": "probe", "probe": "made_up"})
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "analysis_action": "watch", "address": "0x10", "size": 99999})
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "analysis_action": "watch", "address": 1234})
    with pytest.raises(MemoryEngineError):
        ops.dispatch({**base, "identity": {**base["identity"], "start_time_ns": 2}, "analysis_action": "dissect",
                      "address": "0x20000000"})


def test_system_library_regions_are_excluded():
    mem = FakeMemory()
    mem.add(0x1000_0000, 0x1000, writable=False)
    mem.add(0x7000_0000, 0x1000, writable=False)
    mem.module("libsystem_c.dylib", 0x7000_0000, 0x1000, path="/usr/lib/system/libsystem_c.dylib")
    server = MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
    readable, _ = AnalysisOperations(server)._regions(mem, {})
    assert readable == [(0x1000_0000, 0x1000)]
