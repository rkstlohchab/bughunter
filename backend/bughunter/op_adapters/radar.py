"""radar adapter — 2D top-down radar window.

Spawns ``cheats/overlay/radar_window.py`` consuming the universal frame
stream a running ``memory_esp`` recipe is emitting. Multiple overlays can
co-exist (ESP + radar + whatever).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from ._common import envelope


def run(app, *, mode: str = "status",
        shared_file: str | None = None,
        size_px: int = 420, range_m: float = 300.0,
        show_loot: bool = False, rotate: bool = True,
        pid: int | None = None, **_) -> dict:
    if mode == "start":
        return _start(app, shared_file=shared_file, size_px=size_px,
                       range_m=range_m, show_loot=show_loot, rotate=rotate)
    if mode == "stop":
        if not pid:
            raise ValueError("stop requires pid")
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try: os.killpg(os.getpgid(pid), sig); time.sleep(0.2)
            except ProcessLookupError: break
        return envelope("radar",
                        {"command": "stop", "exit_code": 0,
                         "stdout": "", "stderr": "", "duration_ms": 0},
                        pid=pid)
    if mode == "status":
        alive = False
        if pid:
            try: os.kill(pid, 0); alive = True
            except ProcessLookupError: alive = False
        return envelope("radar", {"command": "", "exit_code": 0,
                                   "stdout": "", "stderr": "", "duration_ms": 0},
                        pid=pid, alive=alive)
    raise ValueError(f"unknown mode: {mode}")


def _start(app, *, shared_file, size_px, range_m, show_loot, rotate) -> dict:
    repo = Path(__file__).resolve().parents[3]
    script = repo / "cheats" / "overlay" / "radar_window.py"
    if not shared_file:
        # look for the most recent gamelab_frame_* file
        candidates = sorted(Path("/tmp").glob("gamelab_frame_*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        shared_file = str(candidates[0]) if candidates else "/tmp/gamelab_frame.json"
    cfg = {"input": "shared_file", "shared_file": shared_file,
            "size_px": size_px, "range_m": range_m,
            "rotate_with_camera": rotate, "show_loot": show_loot}
    log_dir = Path(app.store.root) / "gamelab_logs" / "radar"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = log_dir / f"radar-{int(time.time())}.log"
    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    f = open(log, "ab", buffering=0)
    proc = subprocess.Popen(
        ["python3", str(script), "--config-json", json.dumps(cfg)],
        stdout=f, stderr=subprocess.STDOUT, env=env,
        cwd=str(script.parent), start_new_session=True,
    )
    return envelope("radar", {"command": " ".join(["python3", str(script)]),
                               "exit_code": None, "stdout": "",
                               "stderr": "", "duration_ms": 0},
                    pid=proc.pid, log=str(log), shared_file=shared_file,
                    mode="start")
