"""memory_esp adapter — unified memory-ESP pipeline.

Spawns two subprocesses as a group:

1. **Reader** — ``cheats/overlay/memory_esp_runner.py`` running the
   per-game walker, emitting ``Frame`` records to a shared file / UDP.
2. **Overlay** — ``cheats/overlay/esp_overlay.py`` consuming the frames
   and drawing boxes, snaplines, skeletons, HP bars, chams.

Modes:
- ``probe``         — list available games + backends + offsets on disk
- ``start``         — spawn reader + overlay
- ``stop``          — kill both
- ``status``        — tail both logs
- ``restart_reader`` — kill only reader (keep overlay), used after offsets refresh
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
        game: str | None = None,
        reader_backend: str = "dma",       # dma | rpm | android
        process_name: str | None = None,
        mobile_endpoint: str = "127.0.0.1:65510",
        offsets_path: str | None = None,
        project_id: str | None = None,
        target_hz: int = 200,
        screen_w: int = 1920, screen_h: int = 1080,
        udp_enabled: bool = True,
        overlay_mode: str = "opencv",
        chams_mode: str = "team",
        features: dict | None = None,
        pid: int | None = None,
        reader_pid: int | None = None,
        overlay_pid: int | None = None,
        **_) -> dict:
    if mode == "probe":
        return _probe(app)
    if mode == "start":
        if not game:
            raise ValueError("start requires game")
        return _start(app, game=game, reader_backend=reader_backend,
                       process_name=process_name,
                       mobile_endpoint=mobile_endpoint,
                       offsets_path=offsets_path,
                       project_id=project_id,
                       target_hz=target_hz,
                       screen_w=screen_w, screen_h=screen_h,
                       udp_enabled=udp_enabled,
                       overlay_mode=overlay_mode,
                       chams_mode=chams_mode,
                       features=features or {})
    if mode == "stop":
        return _stop(reader_pid=reader_pid, overlay_pid=overlay_pid, pid=pid)
    if mode == "status":
        return _status(app, pid=pid)
    if mode == "restart_reader":
        return _restart_reader(app, pid=reader_pid)
    raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------------


def _probe(app) -> dict:
    from ..game_lab_core.readers import WALKER_BY_GAME
    repo = Path(__file__).resolve().parents[3]
    offsets_dir = Path(app.store.root) / "gamelab_offsets"
    tmpl_dir = repo / "cheats" / "game_data"
    games = []
    for g, cls in WALKER_BY_GAME.items():
        on_disk = (offsets_dir / f"{g}.json").exists()
        template = (tmpl_dir / f"{g}.offsets.template.json").exists()
        games.append({"game": g, "walker": cls.__name__,
                      "anticheat": cls.ANTICHEAT,
                      "has_offsets": on_disk,
                      "has_template": template})
    return envelope("memory_esp",
                    {"command": "probe", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    games=games)


def _start(app, *, game, reader_backend, process_name, mobile_endpoint,
           offsets_path, project_id, target_hz, screen_w, screen_h,
           udp_enabled, overlay_mode, chams_mode, features) -> dict:
    repo = Path(__file__).resolve().parents[3]
    reader = repo / "cheats" / "overlay" / "memory_esp_runner.py"
    overlay = repo / "cheats" / "overlay" / "esp_overlay.py"
    if not reader.exists() or not overlay.exists():
        raise FileNotFoundError("cheats/overlay/ missing")
    # offsets
    if not offsets_path:
        p = Path(app.store.root) / "gamelab_offsets" / f"{game}.json"
        if not p.exists():
            tmpl = repo / "cheats" / "game_data" / f"{game}.offsets.template.json"
            if tmpl.exists():
                p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                p.write_text(tmpl.read_text(), encoding="utf-8")
        offsets_path = str(p)
    shared_file = f"/tmp/gamelab_frame_{game}.json"
    reader_cfg = {
        "game": game, "offsets_path": offsets_path,
        "reader_backend": reader_backend,
        "process_name": process_name,
        "mobile_endpoint": mobile_endpoint,
        "shared_file": shared_file,
        "udp_enabled": udp_enabled, "udp_port": 65501,
        "target_hz": target_hz,
        "screen_w": screen_w, "screen_h": screen_h,
    }
    overlay_cfg = {
        "mode": overlay_mode,
        "input": "shared_file",
        "shared_file": shared_file,
        "width": screen_w, "height": screen_h,
        "chams_mode": chams_mode,
        **{k: v for k, v in features.items() if k.startswith("draw_")},
    }

    log_dir = Path(app.store.root) / "gamelab_logs" / f"memory_esp_{game}"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = int(time.time())
    reader_log = log_dir / f"reader-{stamp}.log"
    overlay_log = log_dir / f"overlay-{stamp}.log"

    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    rf = open(reader_log, "ab", buffering=0)
    reader_proc = subprocess.Popen(
        ["python3", str(reader), "--config-json", json.dumps(reader_cfg)],
        stdout=rf, stderr=subprocess.STDOUT, env=env,
        cwd=str(reader.parent), start_new_session=True,
    )
    time.sleep(0.5)                 # give reader a moment to drop the first frame
    of = open(overlay_log, "ab", buffering=0)
    overlay_proc = subprocess.Popen(
        ["python3", str(overlay), "--config-json", json.dumps(overlay_cfg)],
        stdout=of, stderr=subprocess.STDOUT, env=env,
        cwd=str(overlay.parent), start_new_session=True,
    )
    group_path = log_dir / f"group-{stamp}.json"
    group_path.write_text(json.dumps({
        "reader_pid": reader_proc.pid,
        "overlay_pid": overlay_proc.pid,
        "shared_file": shared_file,
        "reader_log": str(reader_log),
        "overlay_log": str(overlay_log),
    }), encoding="utf-8")
    return envelope("memory_esp",
                    {"command": f"start {game}", "exit_code": None,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=reader_proc.pid,
                    reader_pid=reader_proc.pid, overlay_pid=overlay_proc.pid,
                    shared_file=shared_file,
                    reader_log=str(reader_log),
                    overlay_log=str(overlay_log),
                    mode="start")


def _kill_pid(pid: int | None) -> None:
    if not pid: return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
            time.sleep(0.3)
            os.killpg(os.getpgid(pid), 0)
        except ProcessLookupError:
            return


def _stop(*, reader_pid, overlay_pid, pid) -> dict:
    _kill_pid(reader_pid or pid)
    _kill_pid(overlay_pid)
    return envelope("memory_esp",
                    {"command": "stop", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=pid, mode="stop")


def _status(app, *, pid) -> dict:
    if not pid: return envelope("memory_esp",
                                 {"command": "", "exit_code": -1,
                                  "stdout": "", "stderr": "no pid",
                                  "duration_ms": 0}, alive=False)
    alive = True
    try: os.kill(pid, 0)
    except ProcessLookupError: alive = False
    log_dir_base = Path(app.store.root) / "gamelab_logs"
    tail = ""
    if log_dir_base.exists():
        for sub in log_dir_base.glob("memory_esp_*/reader-*.log"):
            tail = sub.read_bytes()[-4000:].decode("utf-8", errors="replace")
            break
    return envelope("memory_esp",
                    {"command": "status", "exit_code": 0 if alive else -1,
                     "stdout": tail, "stderr": "", "duration_ms": 0},
                    pid=pid, alive=alive)


def _restart_reader(app, *, pid) -> dict:
    _kill_pid(pid)
    return envelope("memory_esp",
                    {"command": "restart_reader", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    reader_pid=pid, mode="restart_reader")
