"""combat adapter — aim + trigger + no-recoil.

Two sub-pipelines:

- ``aim`` — spawns ``cheats/combat/combat_loop.py`` which reads the
  universal Frame, picks a target, computes humanized deltas, sends to
  the configured HID (Arduino / KMBox / ADB).
- ``recoil`` — spawns ``cheats/combat/combat_recoil_writer.py`` which
  writes to the recoil struct every tick. Only works on titles with
  client-authoritative recoil; no-ops on Vanguard / VAC / BE client-side.
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
        feature: str = "aim",           # aim | recoil
        shared_file: str | None = None,
        aim_target: str = "head",
        hid_backend: str = "arduino",
        arduino_port: str | None = None,
        kmbox_endpoint: str | None = None,
        adb_serial: str | None = None,
        fov_px: float = 180.0,
        sens_scale: float = 2.6,
        aim_smooth: float = 0.42,
        tick_ms: int = 90,
        reaction_floor_ms: int = 180,
        miss_rate: float = 0.08,
        deadzone_px: int = 3,
        trigger_enabled: bool = False,
        trigger_fov_px: float = 8.0,
        game: str | None = None,
        process_name: str | None = None,
        reader_backend: str = "dma",
        offsets_path: str | None = None,
        recoil_targets: dict | None = None,
        tick_ms_recoil: int = 20,
        pid: int | None = None,
        **_) -> dict:
    if mode == "start":
        if feature == "aim":
            return _start_aim(app, shared_file=shared_file,
                               aim_target=aim_target,
                               hid_backend=hid_backend,
                               arduino_port=arduino_port,
                               kmbox_endpoint=kmbox_endpoint,
                               adb_serial=adb_serial,
                               fov_px=fov_px, sens_scale=sens_scale,
                               aim_smooth=aim_smooth, tick_ms=tick_ms,
                               reaction_floor_ms=reaction_floor_ms,
                               miss_rate=miss_rate,
                               deadzone_px=deadzone_px,
                               trigger_enabled=trigger_enabled,
                               trigger_fov_px=trigger_fov_px)
        if feature == "recoil":
            return _start_recoil(app, game=game,
                                  process_name=process_name,
                                  reader_backend=reader_backend,
                                  offsets_path=offsets_path,
                                  targets=recoil_targets,
                                  tick_ms=tick_ms_recoil)
        raise ValueError(f"unknown feature: {feature}")
    if mode == "stop":
        if not pid: raise ValueError("stop requires pid")
        for s in (signal.SIGTERM, signal.SIGKILL):
            try: os.killpg(os.getpgid(pid), s); time.sleep(0.2)
            except ProcessLookupError: break
        return envelope("combat",
                        {"command": "stop", "exit_code": 0,
                         "stdout": "", "stderr": "", "duration_ms": 0},
                        pid=pid)
    if mode == "status":
        alive = False
        if pid:
            try: os.kill(pid, 0); alive = True
            except ProcessLookupError: alive = False
        return envelope("combat",
                        {"command": "", "exit_code": 0,
                         "stdout": "", "stderr": "", "duration_ms": 0},
                        pid=pid, alive=alive)
    raise ValueError(f"unknown mode: {mode}")


def _start_aim(app, **cfg_kwargs) -> dict:
    repo = Path(__file__).resolve().parents[3]
    script = repo / "cheats" / "combat" / "combat_loop.py"
    if not cfg_kwargs.get("shared_file"):
        candidates = sorted(Path("/tmp").glob("gamelab_frame_*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        cfg_kwargs["shared_file"] = str(candidates[0]) if candidates else "/tmp/gamelab_frame.json"
    cfg = {
        "input_path": cfg_kwargs["shared_file"],
        "aim_target": cfg_kwargs["aim_target"],
        "hid_backend": cfg_kwargs["hid_backend"],
        "arduino_port": cfg_kwargs["arduino_port"],
        "kmbox_endpoint": cfg_kwargs["kmbox_endpoint"],
        "adb_serial": cfg_kwargs["adb_serial"],
        "fov_px": cfg_kwargs["fov_px"],
        "sens_scale": cfg_kwargs["sens_scale"],
        "aim_smooth": cfg_kwargs["aim_smooth"],
        "tick_ms": cfg_kwargs["tick_ms"],
        "reaction_floor_ms": cfg_kwargs["reaction_floor_ms"],
        "miss_rate": cfg_kwargs["miss_rate"],
        "deadzone_px": cfg_kwargs["deadzone_px"],
        "trigger_enabled": cfg_kwargs["trigger_enabled"],
        "trigger_fov_px": cfg_kwargs["trigger_fov_px"],
    }
    log_dir = Path(app.store.root) / "gamelab_logs" / "combat_aim"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = log_dir / f"aim-{int(time.time())}.log"
    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    f = open(log, "ab", buffering=0)
    proc = subprocess.Popen(
        ["python3", str(script), "--config-json", json.dumps(cfg)],
        stdout=f, stderr=subprocess.STDOUT, env=env,
        cwd=str(script.parent), start_new_session=True,
    )
    return envelope("combat", {"command": "aim.start", "exit_code": None,
                                "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=proc.pid, feature="aim", log=str(log), mode="start")


def _start_recoil(app, *, game, process_name, reader_backend, offsets_path,
                    targets, tick_ms) -> dict:
    if not game or not process_name:
        raise ValueError("recoil needs game + process_name")
    repo = Path(__file__).resolve().parents[3]
    script = repo / "cheats" / "combat" / "combat_recoil_writer.py"
    if not offsets_path:
        offsets_path = str(Path(app.store.root) / "gamelab_offsets" / f"{game}.json")
    cfg = {
        "game": game, "offsets_path": offsets_path,
        "reader_backend": reader_backend,
        "process_name": process_name,
        "targets": targets, "tick_ms": tick_ms,
    }
    log_dir = Path(app.store.root) / "gamelab_logs" / "combat_recoil"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = log_dir / f"recoil-{int(time.time())}.log"
    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    f = open(log, "ab", buffering=0)
    proc = subprocess.Popen(
        ["python3", str(script), "--config-json", json.dumps(cfg)],
        stdout=f, stderr=subprocess.STDOUT, env=env,
        cwd=str(script.parent), start_new_session=True,
    )
    return envelope("combat", {"command": "recoil.start", "exit_code": None,
                                "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=proc.pid, feature="recoil", log=str(log), mode="start")
