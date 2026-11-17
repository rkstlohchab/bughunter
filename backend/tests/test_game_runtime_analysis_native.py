"""Analysis lane through the real macOS adapter against a disposable target process.

Proves the probe/watch code over real process memory and the real attach
path. It is not a game result.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bughunter.memory_engine.analysis import AnalysisOperations  # noqa: E402
from bughunter.memory_engine.ownership import ExecutorRegistry  # noqa: E402
from bughunter.memory_engine.protocol import MemoryEngineUnavailable, ProcessIdentity, read_process_start_time_ns  # noqa: E402
from bughunter.memory_engine.server import MemoryEngineServer, ServiceConfig  # noqa: E402

TARGET = Path(__file__).resolve().parent / "_engine_target.py"


@pytest.mark.skipif(sys.platform != "darwin", reason="mac adapter test")
def test_probe_and_watch_through_real_adapter():
    from bughunter.memory_engine.mac_adapter import MacMemoryAdapter
    proc = subprocess.Popen([sys.executable, "-I", str(TARGET)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    server = None
    try:
        layout = json.loads(proc.stdout.readline())
        identity = ProcessIdentity(pid=proc.pid, start_time_ns=read_process_start_time_ns(proc.pid), target_fingerprint="fixture")
        try:
            adapter = MacMemoryAdapter(proc.pid, identity=identity)
        except MemoryEngineUnavailable as exc:
            pytest.skip(f"mac_adapter unavailable: {exc}")
        server = MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
        assert server.attach(adapter, session_id="exec")["status"] == "attached"
        ops = AnalysisOperations(server)
        base = {"session_id": "exec", "identity": identity.to_wire(), "task_session_id": "task",
                "regions": [{"base": hex(layout["meta"]), "size": 0x2000}, {"base": hex(layout["heap"]), "size": 0x10000}]}
        result = ops.dispatch({**base, "analysis_action": "probe", "probe": "il2cpp_class", "name": "PlayerWeapon"})
        while not result["complete"]:
            result = ops.dispatch({**base, "analysis_action": "probe", "job_id": result["job_id"]})
        best = result["result"]["candidates"][0]
        assert best["class_address"] == hex(layout["weapon"]) and best["name_slot_offset"] == "0x10"
        assert best["namespace"] == "Game.Combat"
        obj = layout["instances"][0]
        watch = ops.dispatch({**base, "analysis_action": "watch", "address": hex(obj), "size": 64,
                              "duration_s": 1.5, "interval_ms": 50})
        assert watch["samples"] >= 5, watch
        row = next(r for r in watch["windows"][0]["rows"] if r["offset"] == "0x24")
        assert row["type"] == "i32" and row["changes"] >= 3
        dissect = ops.dispatch({**base, "analysis_action": "dissect", "address": hex(obj), "size": 64, "il2cpp_name_slot": "0x10"})
        assert dissect["object_class"] == "PlayerWeapon"
        ops.close()
    finally:
        if server is not None:
            server.shutdown()
        proc.stdin.close()
        proc.wait(timeout=10)
