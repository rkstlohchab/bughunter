"""mac_appstore_memory_esp adapter — Mac App Store / direct-install Mac
app memory ESP via dylib injection.

For Among Us installed from the Mac App Store (no AC on that build) +
any other Unity/Unreal Mac app that'll accept an ad-hoc re-sign with
library-validation disabled.

Flow:
1. User installs the app from the Mac App Store normally.
2. ``build_dylib`` compiles the macOS variant of gamelab_memory.dylib.
3. ``patch_app`` copies the stock .app to a working dir, inserts
   LC_LOAD_DYLIB, copies the dylib into Contents/Frameworks/, and
   re-signs ad-hoc with Hardened-Runtime-breaking entitlements.
4. ``launch`` opens the patched .app.
5. ``push_offsets`` ships offsets.json over localhost:65510.
6. ``start`` chains all of the above + spawns the memory_esp reader
   and universal overlay.

No SIP changes needed. No Apple Developer account needed. First launch
may require right-click → Open to clear Gatekeeper for ad-hoc signed
apps.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from ._common import envelope, locate, run_cmd


REPO = Path(__file__).resolve().parents[3]
IOS_DIR = REPO / "cheats" / "mobile-bgmi" / "ios_memory"


def run(app, *, mode: str = "check_build",
        app_in: str | None = None,
        app_out: str | None = None,
        game: str = "among_us",
        offsets_path: str | None = None,
        port: int = 65510,
        bundle_id: str | None = None,
        pid: int | None = None,
        reader_pid: int | None = None,
        overlay_pid: int | None = None,
        **extras) -> dict:
    if mode == "check_build":
        return _check_build()
    if mode == "build_dylib":
        return _build_dylib()
    if mode == "patch_app":
        return _patch_app(app_in=app_in, app_out=app_out)
    if mode == "launch":
        return _launch(app_out=app_out)
    if mode == "push_offsets":
        return _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    if mode == "health_check":
        return _health_check(port=port)
    if mode == "start":
        return _start(app, app_out=app_out, game=game,
                       offsets_path=offsets_path, port=port, **extras)
    if mode == "stop":
        return _stop(pid=pid, reader_pid=reader_pid, overlay_pid=overlay_pid)
    raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------


def _check_build() -> dict:
    checks = {}
    for name, hint in (
        ("xcrun",        "xcode-select --install"),
        ("clang++",      "xcode-select --install"),
        ("insert_dylib", "brew install insert_dylib"),
        ("codesign",     "preinstalled on macOS"),
    ):
        try:
            locate(name)
            checks[name] = True
        except Exception:
            checks[name] = hint
    among = Path("/Applications/Among Us.app")
    checks["among_us_installed"] = among.exists()
    missing = [k for k, v in checks.items() if v not in (True,)]
    return envelope("mac_appstore_memory_esp",
                    {"command": "check_build",
                     "exit_code": 0 if all(v is True or k == "among_us_installed" for k, v in checks.items()) else 1,
                     "stdout": json.dumps(checks, indent=2),
                     "stderr": "", "duration_ms": 0},
                    checks=checks, missing=missing)


def _build_dylib() -> dict:
    script = IOS_DIR / "scripts" / "build_dylib_macos.sh"
    out = run_cmd(["bash", str(script)], timeout=180)
    return envelope("mac_appstore_memory_esp", out,
                    dylib=str(IOS_DIR / "build" / "gamelab_memory_mac.dylib"))


def _patch_app(*, app_in: str | None, app_out: str | None) -> dict:
    if not app_in:
        # default: Mac App Store Among Us
        app_in = "/Applications/Among Us.app"
    if not Path(app_in).exists():
        return envelope("mac_appstore_memory_esp",
                        {"command": "patch_app", "exit_code": -1,
                         "stdout": "", "stderr": f"app not found: {app_in}",
                         "duration_ms": 0})
    if not app_out:
        app_out = str(Path.home() / "gamelab-work" / Path(app_in).name)
    dylib = IOS_DIR / "build" / "gamelab_memory_mac.dylib"
    if not dylib.exists():
        return envelope("mac_appstore_memory_esp",
                        {"command": "patch_app", "exit_code": -1,
                         "stdout": "", "stderr": "dylib not built — run build_dylib first",
                         "duration_ms": 0})
    script = IOS_DIR / "scripts" / "patch_mac_app.sh"
    out = run_cmd(["bash", str(script),
                   "--app", app_in,
                   "--dylib", str(dylib),
                   "--out", app_out], timeout=120)
    return envelope("mac_appstore_memory_esp", out,
                    app_out=app_out, dylib=str(dylib))


def _launch(*, app_out: str | None) -> dict:
    if not app_out:
        raise ValueError("launch needs app_out (patched .app path)")
    out = run_cmd(["open", app_out], timeout=10)
    return envelope("mac_appstore_memory_esp", out, app_out=app_out)


def _push_offsets(app, *, game: str, offsets_path: str | None, port: int) -> dict:
    # Same wire protocol as iOS/Android bridges — reuse helper.
    from . import ios_memory_esp
    return ios_memory_esp.run(app, mode="push_offsets",
                                game=game, offsets_path=offsets_path, port=port)


def _health_check(*, port: int) -> dict:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("127.0.0.1", port))
        s.sendall(b'{"cmd":"ping"}\n')
        resp = s.recv(64)
        s.close()
        ok = b'"pong":1' in resp
        return envelope("mac_appstore_memory_esp",
                        {"command": "health_check",
                         "exit_code": 0 if ok else 1,
                         "stdout": resp.decode(errors="replace"),
                         "stderr": "", "duration_ms": 0},
                        ok=ok, port=port)
    except Exception as exc:
        return envelope("mac_appstore_memory_esp",
                        {"command": "health_check", "exit_code": -1,
                         "stdout": "", "stderr": str(exc),
                         "duration_ms": 0}, ok=False, port=port)


def _start(app, *, app_out, game, offsets_path, port, **extras) -> dict:
    if not app_out:
        app_out = str(Path.home() / "gamelab-work" / "Among Us.app")
    # launch if not running
    _launch(app_out=app_out)
    # The dylib's constructor is deferred 5-9s for launch-timing jitter;
    # wait before pinging.
    time.sleep(11)
    hc = _health_check(port=port)
    if hc.get("exit_code") != 0:
        return envelope("mac_appstore_memory_esp",
                        {"command": "start", "exit_code": -1,
                         "stdout": hc.get("stdout", ""),
                         "stderr": "dylib not responding on localhost:"
                                    f"{port} — check Console.app for 'gamelab_memory' logs.",
                         "duration_ms": 0})
    push = _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    from . import memory_esp
    reader = memory_esp.run(
        app, mode="start", game=game,
        reader_backend="android",       # dylib speaks the android/ios protocol
        mobile_endpoint=f"127.0.0.1:{port}",
        offsets_path=offsets_path,
        **{k: v for k, v in extras.items()
           if k in ("project_id", "target_hz", "screen_w", "screen_h",
                    "overlay_mode", "chams_mode", "features")},
    )
    reader["push_offsets_result"] = push
    reader["app_out"] = app_out
    reader["via"] = "mac_appstore"
    return reader


def _stop(*, pid, reader_pid, overlay_pid) -> dict:
    from . import memory_esp
    return memory_esp.run(None, mode="stop", pid=pid,
                            reader_pid=reader_pid, overlay_pid=overlay_pid)
