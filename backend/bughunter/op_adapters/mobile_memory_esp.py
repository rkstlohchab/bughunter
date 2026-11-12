"""mobile_memory_esp adapter — Zygisk bridge + PC-side memory ESP.

Flow:

1. **Device-side Zygisk module** (``cheats/mobile-bgmi/zygisk_memory/``)
   is installed on the rooted phone and hidden via Shamiko DenyList.
   It opens an abstract UNIX socket inside the game process.
2. **adb forward** bridges that socket to a PC TCP port.
3. This adapter runs the ``memory_esp`` pipeline with
   ``reader_backend="android"``.
4. On first connect, this adapter sends the game's offset JSON to the
   phone's Zygisk module via the ``set_offsets`` socket command, so the
   phone doesn't need per-patch re-flashes.

Verbs:
- ``check_deploy``  — verify Zygisk module installed + Shamiko ready
- ``forward``       — set up ``adb forward tcp:65510 localabstract:gamelab_mem``
- ``push_offsets``  — send offsets.json to the Zygisk module
- ``start``         — same as ``memory_esp start game=bgmi reader_backend=android``
- ``stop``          — tear everything down
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from ._common import envelope, locate, run_cmd


def run(app, *, mode: str = "check_deploy",
        serial: str | None = None,
        host_port: int = 65510,
        game: str = "bgmi",
        offsets_path: str | None = None,
        **extras) -> dict:
    if mode == "check_deploy":
        return _check_deploy(serial)
    if mode == "forward":
        return _forward(serial, host_port)
    if mode == "push_offsets":
        return _push_offsets(app, game=game, offsets_path=offsets_path,
                              host_port=host_port)
    if mode == "start":
        return _start_bridge_and_reader(app, serial=serial, game=game,
                                         host_port=host_port,
                                         offsets_path=offsets_path, **extras)
    if mode == "stop":
        return _stop(serial, host_port, extras.get("pid"),
                     extras.get("reader_pid"), extras.get("overlay_pid"))
    raise ValueError(f"unknown mode: {mode}")


def _check_deploy(serial: str | None) -> dict:
    adb = locate("adb", hint="brew install android-platform-tools")
    base = [adb] + (["-s", serial] if serial else [])
    checks = {}
    # Magisk installed?
    out = run_cmd(base + ["shell", "su", "-c", "magisk -V"], timeout=5)
    checks["magisk_version"] = (out["stdout"] or "").strip()
    # Zygisk on?
    out = run_cmd(base + ["shell", "su", "-c", "magisk --zygisk-status"], timeout=5)
    checks["zygisk_status"] = (out["stdout"] or "").strip()
    # module installed?
    out = run_cmd(base + ["shell", "su", "-c",
                           "ls /data/adb/modules | grep gamelab_memory || true"],
                  timeout=5)
    checks["module_installed"] = bool((out["stdout"] or "").strip())
    # denylist enforcing?
    out = run_cmd(base + ["shell", "su", "-c", "magisk --denylist status"], timeout=5)
    checks["denylist"] = (out["stdout"] or "").strip()
    flags = []
    if not checks["module_installed"]:
        flags.append("gamelab_memory module not installed (flash gamelab_memory.zip)")
    if "Zygisk" not in checks["zygisk_status"] and "enabled" not in checks["zygisk_status"].lower():
        flags.append("Zygisk not enabled")
    return envelope("mobile_memory_esp",
                    {"command": "check_deploy",
                     "exit_code": 0 if not flags else 1,
                     "stdout": json.dumps(checks, indent=2),
                     "stderr": "", "duration_ms": 0},
                    flags=flags, checks=checks)


def _forward(serial: str | None, host_port: int) -> dict:
    adb = locate("adb")
    base = [adb] + (["-s", serial] if serial else [])
    out = run_cmd(base + ["forward", f"tcp:{host_port}",
                           "localabstract:gamelab_mem"], timeout=10)
    return envelope("mobile_memory_esp", out,
                    host_port=host_port, forwarded=(out["exit_code"] == 0))


def _push_offsets(app, *, game, offsets_path, host_port) -> dict:
    if not offsets_path:
        offsets_path = str(Path(app.store.root) / "gamelab_offsets" / f"{game}.json")
    if not Path(offsets_path).exists():
        repo = Path(__file__).resolve().parents[3]
        tmpl = repo / "cheats" / "game_data" / f"{game}.offsets.template.json"
        if tmpl.exists():
            Path(offsets_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            Path(offsets_path).write_text(tmpl.read_text(), encoding="utf-8")
    blob = json.loads(Path(offsets_path).read_text())
    offs = blob.get("offsets", blob)
    payload = {"cmd": "set_offsets", **_shape_for_mobile(offs)}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(("127.0.0.1", host_port))
        s.sendall((json.dumps(payload) + "\n").encode())
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = s.recv(1)
            if not chunk: break
            reply += chunk
        s.close()
        return envelope("mobile_memory_esp",
                        {"command": "push_offsets", "exit_code": 0,
                         "stdout": reply.decode(errors="replace"),
                         "stderr": "", "duration_ms": 0},
                        game=game, offsets_path=offsets_path)
    except Exception as exc:
        return envelope("mobile_memory_esp",
                        {"command": "push_offsets", "exit_code": -1,
                         "stdout": "", "stderr": str(exc),
                         "duration_ms": 0},
                        game=game, offsets_path=offsets_path)


def _shape_for_mobile(offs: dict) -> dict:
    """Flatten the bone subtable into bone_0..bone_16 for the Zygisk C++ parser."""
    flat = {k: v for k, v in offs.items() if not isinstance(v, dict)}
    if "bones" in offs and isinstance(offs["bones"], dict):
        names = ["head","neck","pelvis","spine_up","spine_lo",
                 "shoulder_l","elbow_l","hand_l",
                 "shoulder_r","elbow_r","hand_r",
                 "hip_l","knee_l","foot_l",
                 "hip_r","knee_r","foot_r"]
        for i, n in enumerate(names):
            if n in offs["bones"]:
                flat[f"bone_{i}"] = int(offs["bones"][n])
    return flat


def _start_bridge_and_reader(app, *, serial, game, host_port,
                              offsets_path, **extras) -> dict:
    # 1) forward
    fwd = _forward(serial, host_port)
    if fwd.get("exit_code") != 0:
        return fwd
    # 2) push offsets (best-effort)
    push = _push_offsets(app, game=game, offsets_path=offsets_path,
                         host_port=host_port)
    # 3) start memory_esp with android backend
    from . import memory_esp
    reader = memory_esp.run(
        app, mode="start", game=game,
        reader_backend="android",
        mobile_endpoint=f"127.0.0.1:{host_port}",
        offsets_path=offsets_path,
        **{k: v for k, v in extras.items()
           if k in ("project_id", "target_hz", "screen_w", "screen_h",
                    "overlay_mode", "chams_mode", "features")},
    )
    reader["push_offsets_result"] = push
    return reader


def _stop(serial, host_port, pid, reader_pid, overlay_pid) -> dict:
    from . import memory_esp
    out = memory_esp.run(None, mode="stop", pid=pid,
                          reader_pid=reader_pid, overlay_pid=overlay_pid)
    adb = locate("adb")
    base = [adb] + (["-s", serial] if serial else [])
    run_cmd(base + ["forward", "--remove", f"tcp:{host_port}"], timeout=5)
    return out
