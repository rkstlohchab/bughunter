"""among_us_cheat adapter — no-cooldown kill + fog-off + role-reveal ESP.

Three sub-features (any combination):
- ``wallhack``     — team-colored overlay boxes, fog disabled
- ``no_cooldown``  — 50 Hz write of PlayerControl.killTimer = 0 on LocalPlayer
- ``role_reveal``  — name tag colored by IsImpostor from PlayerInfo

All three consume the universal Frame emitted by the standard
memory_esp pipeline. This adapter owns the write-loop subprocess only;
the Frame + overlay run independently.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from ._common import envelope


REPO = Path(__file__).resolve().parents[3]


def run(app, *, mode: str = "status",
        feature: str = "all",            # wallhack | no_cooldown | role_reveal | all
        game: str = "among_us",
        reader_backend: str = "dma",
        process_name: str | None = None,
        mobile_endpoint: str = "127.0.0.1:65510",
        offsets_path: str | None = None,
        tick_ms: int = 20,
        project_id: str | None = None,
        pid: int | None = None,
        **_) -> dict:
    if mode == "start":
        return _start(app, feature=feature, game=game,
                       reader_backend=reader_backend,
                       process_name=process_name,
                       mobile_endpoint=mobile_endpoint,
                       offsets_path=offsets_path,
                       tick_ms=tick_ms,
                       project_id=project_id)
    if mode == "stop":
        return _stop(pid)
    if mode == "status":
        return _status(pid)
    raise ValueError(f"unknown mode: {mode}")


def _default_process(backend: str) -> str:
    return {
        "dma": "Among Us.exe",
        "rpm": "Among Us.exe",
        "android": "com.innersloth.spacemafia",
    }.get(backend, "Among Us")


def _start(app, *, feature, game, reader_backend, process_name,
           mobile_endpoint, offsets_path, tick_ms, project_id) -> dict:
    if not offsets_path:
        p = Path(app.store.root) / "gamelab_offsets" / f"{game}.json"
        if not p.exists():
            tmpl = REPO / "cheats" / "game_data" / f"{game}.offsets.template.json"
            if tmpl.exists():
                p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                p.write_text(tmpl.read_text(), encoding="utf-8")
        offsets_path = str(p)
    cfg = {
        "feature": feature, "game": game,
        "reader_backend": reader_backend,
        "process_name": process_name or _default_process(reader_backend),
        "mobile_endpoint": mobile_endpoint,
        "offsets_path": offsets_path,
        "tick_ms": tick_ms,
        "project_id": project_id,
    }
    runner = REPO / "cheats" / "overlay" / "among_us_runner.py"
    log_dir = Path(app.store.root) / "gamelab_logs" / "among_us_cheat"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / f"run-{int(time.time())}.log"
    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    f = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        ["python3", str(runner), "--config-json", json.dumps(cfg)],
        stdout=f, stderr=subprocess.STDOUT, env=env,
        cwd=str(runner.parent), start_new_session=True,
    )
    return envelope("among_us_cheat",
                    {"command": f"start {feature}", "exit_code": None,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=proc.pid, log=str(log_path), feature=feature)


def _stop(pid: int | None) -> dict:
    if not pid:
        raise ValueError("stop requires pid")
    for s in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(os.getpgid(pid), s); time.sleep(0.2)
        except ProcessLookupError: break
    return envelope("among_us_cheat",
                    {"command": "stop", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=pid)


def _status(pid: int | None) -> dict:
    alive = False
    if pid:
        try: os.kill(pid, 0); alive = True
        except ProcessLookupError: alive = False
    return envelope("among_us_cheat",
                    {"command": "status", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=pid, alive=alive)
