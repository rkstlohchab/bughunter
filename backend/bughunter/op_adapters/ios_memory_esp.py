"""ios_memory_esp adapter — BGMI (and other UE4 games) on non-jailbroken iOS.

Flow:
1. Patched BGMI runs on the iPhone with the ``gamelab_memory.dylib``
   injected. The dylib opens a localhost TCP server on port 65510 inside
   the sandbox.
2. On the Mac, ``iproxy -u <udid> 65510 65510`` bridges usbmuxd →
   localhost:65510.
3. This adapter spawns the ``memory_esp`` pipeline with
   ``reader_backend="android"`` — the Android bridge speaks the exact
   same wire protocol as the iOS dylib, so the universal overlay works
   unchanged.

Modes:
- ``check_build``   — verify Xcode CLT, insert_dylib, libimobiledevice present
- ``build_dylib``   — compile gamelab_memory.dylib via cheats/mobile-bgmi/ios_memory/scripts/build_dylib.sh
- ``patch_ipa``     — insert LC_LOAD_DYLIB + copy dylib into the app's Frameworks/
- ``sideload``      — hand the patched IPA to AltServer or Sideloadly
- ``forward``       — start iproxy forward
- ``push_offsets``  — send bgmi.offsets.json to the dylib over the socket
- ``start``         — forward + push_offsets + spawn reader + overlay
- ``stop``          — kill forward + reader + overlay
- ``health_check``  — ping the dylib
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


REPO_ROOT = Path(__file__).resolve().parents[3]
IOS_DIR = REPO_ROOT / "cheats" / "mobile-bgmi" / "ios_memory"


def run(app, *, mode: str = "check_build",
        udid: str | None = None,
        port: int = 65510,
        game: str = "bgmi",
        offsets_path: str | None = None,
        ipa_in: str | None = None,
        ipa_out: str | None = None,
        via: str = "altstore",
        pid: int | None = None,
        reader_pid: int | None = None,
        overlay_pid: int | None = None,
        iproxy_pid: int | None = None,
        **extras) -> dict:
    if mode == "check_build":
        return _check_build()
    if mode == "build_dylib":
        return _build_dylib()
    if mode == "patch_ipa":
        return _patch_ipa(ipa_in=ipa_in, ipa_out=ipa_out)
    if mode == "sideload":
        return _sideload(ipa=ipa_out or ipa_in, via=via)
    if mode == "forward":
        return _forward(udid=udid, port=port)
    if mode == "push_offsets":
        return _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    if mode == "health_check":
        return _health_check(udid=udid, port=port)
    if mode == "start":
        return _start(app, udid=udid, port=port, game=game,
                       offsets_path=offsets_path, **extras)
    if mode == "stop":
        return _stop(udid=udid, port=port, pid=pid,
                     reader_pid=reader_pid, overlay_pid=overlay_pid,
                     iproxy_pid=iproxy_pid)
    raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------


def _check_build() -> dict:
    checks = {}
    for name, hint in (
        ("xcrun",         "xcode-select --install"),
        ("clang++",       "xcode-select --install"),
        ("insert_dylib",  "brew install insert_dylib"),
        ("iproxy",        "brew install libimobiledevice"),
        ("idevice_id",    "brew install libimobiledevice"),
        ("altserver",     "GUI: install AltServer from https://altstore.io"),
        ("unzip",         "preinstalled on macOS"),
        ("zip",           "preinstalled on macOS"),
    ):
        try:
            locate(name)
            checks[name] = True
        except Exception:
            checks[name] = hint
    missing = [k for k, v in checks.items() if v is not True]
    return envelope("ios_memory_esp",
                    {"command": "check_build",
                     "exit_code": 0 if not missing else 1,
                     "stdout": json.dumps(checks, indent=2),
                     "stderr": "", "duration_ms": 0},
                    missing=missing, checks=checks)


def _build_dylib() -> dict:
    script = IOS_DIR / "scripts" / "build_dylib.sh"
    if not script.exists():
        raise FileNotFoundError(f"missing {script}")
    out = run_cmd(["bash", str(script)], timeout=180)
    return envelope("ios_memory_esp", out,
                    dylib=str(IOS_DIR / "build" / "gamelab_memory.dylib"))


def _patch_ipa(*, ipa_in: str | None, ipa_out: str | None) -> dict:
    if not ipa_in:
        raise ValueError("patch_ipa requires ipa_in (your extracted stock BGMI IPA)")
    if not ipa_out:
        ipa_out = str(Path(ipa_in).with_name("BGMI-gamelab.ipa"))
    dylib = IOS_DIR / "build" / "gamelab_memory.dylib"
    if not dylib.exists():
        return envelope("ios_memory_esp",
                        {"command": "patch_ipa", "exit_code": -1,
                         "stdout": "", "stderr": "dylib not built — run build_dylib first",
                         "duration_ms": 0})
    script = IOS_DIR / "scripts" / "patch_ipa.sh"
    out = run_cmd(["bash", str(script), "--ipa", ipa_in,
                    "--dylib", str(dylib), "--out", ipa_out], timeout=120)
    return envelope("ios_memory_esp", out, ipa_out=ipa_out)


def _sideload(*, ipa: str | None, via: str) -> dict:
    if not ipa:
        raise ValueError("sideload requires an IPA path")
    script = IOS_DIR / "scripts" / "sideload.sh"
    out = run_cmd(["bash", str(script), "--ipa", ipa, "--via", via], timeout=60)
    return envelope("ios_memory_esp", out, via=via, ipa=ipa)


def _forward(*, udid: str | None, port: int) -> dict:
    if not udid:
        try:
            udid = run_cmd([locate("idevice_id"), "-l"], timeout=5)["stdout"].strip().splitlines()[0]
        except Exception:
            raise ValueError("no paired iPhone and no --udid provided")
    # Kill stale
    subprocess.run(["pkill", "-f", f"iproxy {port} {port}"], capture_output=True)
    iproxy = locate("iproxy", hint="brew install libimobiledevice")
    log_dir = Path("/tmp")
    log_path = log_dir / f"gamelab_iproxy_{port}.log"
    f = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        [iproxy, "-u", udid, str(port), str(port)],
        stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
    )
    time.sleep(0.4)
    return envelope("ios_memory_esp",
                    {"command": f"iproxy -u {udid} {port} {port}",
                     "exit_code": 0, "stdout": "", "stderr": "",
                     "duration_ms": 0},
                    iproxy_pid=proc.pid, udid=udid, port=port,
                    log=str(log_path))


def _push_offsets(app, *, game: str, offsets_path: str | None, port: int) -> dict:
    if not offsets_path:
        p = Path(app.store.root) / "gamelab_offsets" / f"{game}.json"
        if not p.exists():
            tmpl = REPO_ROOT / "cheats" / "game_data" / f"{game}.offsets.template.json"
            if tmpl.exists():
                p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                p.write_text(tmpl.read_text(), encoding="utf-8")
        offsets_path = str(p)
    if not Path(offsets_path).exists():
        return envelope("ios_memory_esp",
                        {"command": "push_offsets", "exit_code": -1,
                         "stdout": "", "stderr": f"offsets missing: {offsets_path}",
                         "duration_ms": 0})
    blob = json.loads(Path(offsets_path).read_text())
    offs = blob.get("offsets", blob)
    # Flatten bones subtable to bone_0..bone_16 for the dylib
    payload = {"cmd": "set_offsets"}
    for k, v in offs.items():
        if not isinstance(v, dict):
            payload[k] = v
    if isinstance(offs.get("bones"), dict):
        names = ["head","neck","pelvis","spine_up","spine_lo",
                 "shoulder_l","elbow_l","hand_l",
                 "shoulder_r","elbow_r","hand_r",
                 "hip_l","knee_l","foot_l",
                 "hip_r","knee_r","foot_r"]
        for i, n in enumerate(names):
            if n in offs["bones"]:
                payload[f"bone_{i}"] = int(offs["bones"][n])
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
        s.sendall((json.dumps(payload) + "\n").encode())
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = s.recv(1)
            if not chunk: break
            reply += chunk
        s.close()
        return envelope("ios_memory_esp",
                        {"command": "push_offsets", "exit_code": 0,
                         "stdout": reply.decode(errors="replace"),
                         "stderr": "", "duration_ms": 0},
                        game=game, offsets_path=offsets_path)
    except Exception as exc:
        return envelope("ios_memory_esp",
                        {"command": "push_offsets", "exit_code": -1,
                         "stdout": "", "stderr": str(exc),
                         "duration_ms": 0})


def _health_check(*, udid: str | None, port: int) -> dict:
    script = IOS_DIR / "scripts" / "health_check.sh"
    args = ["bash", str(script)]
    if udid: args.append(udid)
    out = run_cmd(args, timeout=10)
    return envelope("ios_memory_esp", out, udid=udid, port=port)


def _start(app, *, udid, port, game, offsets_path, **extras) -> dict:
    # 1) iproxy forward
    fwd = _forward(udid=udid, port=port)
    iproxy_pid = fwd.get("iproxy_pid")
    # 2) health check
    hc = _health_check(udid=udid, port=port)
    if hc.get("exit_code") != 0:
        return envelope("ios_memory_esp",
                        {"command": "start", "exit_code": -1,
                         "stdout": hc.get("stdout", ""),
                         "stderr": "dylib not responding — see health_check output",
                         "duration_ms": 0},
                        iproxy_pid=iproxy_pid)
    # 3) push offsets
    push = _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    # 4) start memory_esp with android backend (same wire protocol)
    from . import memory_esp
    reader = memory_esp.run(
        app, mode="start", game=game,
        reader_backend="android",
        mobile_endpoint=f"127.0.0.1:{port}",
        offsets_path=offsets_path,
        **{k: v for k, v in extras.items()
           if k in ("project_id", "target_hz", "screen_w", "screen_h",
                    "overlay_mode", "chams_mode", "features")},
    )
    reader["iproxy_pid"] = iproxy_pid
    reader["push_offsets_result"] = push
    reader["udid"] = udid
    return reader


def _stop(*, udid, port, pid, reader_pid, overlay_pid, iproxy_pid) -> dict:
    from . import memory_esp
    out = memory_esp.run(None, mode="stop", pid=pid,
                          reader_pid=reader_pid, overlay_pid=overlay_pid)
    # kill iproxy
    if iproxy_pid:
        try: os.killpg(os.getpgid(iproxy_pid), signal.SIGTERM)
        except ProcessLookupError: pass
    subprocess.run(["pkill", "-f", f"iproxy {port} {port}"], capture_output=True)
    return out
