"""playcover_memory_esp adapter — M1/M2/M3 Mac path for memory ESP.

PlayCover runs iOS arm64 apps natively on Apple Silicon Macs. The
``gamelab_memory.dylib`` we built for iPhone works unchanged — it's an
iOS arm64 Mach-O, loaded into the game process by PlayCover's own
``DYLD_INSERT_LIBRARIES`` hook.

Flow:

1. PlayCover (brew install --cask playcover-community) is installed.
2. User drops the patched IPA onto PlayCover; it signs with a local
   cert and installs under ``~/Library/Containers/io.playcover.PlayCover/
   Applications/``.
3. On launch, PlayCover runs the app as a native Mac window. The
   dylib's constructor fires. It opens 127.0.0.1:65510.
4. This adapter talks to localhost:65510 directly — no iproxy, no
   usbmuxd, because the game is running on the same machine as the
   bughunter desktop.

Modes:
- ``check_playcover``   — verify PlayCover is installed + signing cert configured
- ``install_ipa``       — hand the patched IPA to PlayCover for install
- ``launch_app``        — open the installed app via PlayCover
- ``push_offsets``      — push offsets.json over localhost:65510
- ``start``             — full kit: launch + push offsets + spawn reader + overlay
- ``stop``              — kill reader + overlay (game left running)
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

from ._common import envelope, run_cmd


REPO = Path(__file__).resolve().parents[3]
PLAYCOVER_APP_DIR = Path.home() / "Library" / "Containers" / "io.playcover.PlayCover" / "Applications"


def run(app, *, mode: str = "check_playcover",
        ipa: str | None = None,
        bundle_id: str | None = None,
        game: str = "among_us",
        offsets_path: str | None = None,
        port: int = 65510,
        pid: int | None = None,
        reader_pid: int | None = None,
        overlay_pid: int | None = None,
        **extras) -> dict:
    if mode == "check_playcover":
        return _check()
    if mode == "install_ipa":
        return _install_ipa(ipa=ipa)
    if mode == "launch_app":
        return _launch_app(bundle_id=bundle_id)
    if mode == "push_offsets":
        return _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    if mode == "start":
        return _start(app, bundle_id=bundle_id, game=game,
                       offsets_path=offsets_path, port=port, **extras)
    if mode == "stop":
        return _stop(pid=pid, reader_pid=reader_pid, overlay_pid=overlay_pid)
    raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------


def _check() -> dict:
    checks = {}
    app_path = Path("/Applications/PlayCover.app")
    checks["playcover_installed"] = app_path.exists()
    # PlayCover signing cert — self-signed local cert under login keychain
    cert = run_cmd(["security", "find-identity", "-v", "-p", "codesigning"],
                    timeout=5)
    checks["signing_identities"] = cert["stdout"].strip().splitlines()[:5]
    checks["have_identity"] = any("PlayCover" in line or "Apple Development" in line
                                    for line in checks["signing_identities"])
    checks["playcover_container"] = PLAYCOVER_APP_DIR.exists()
    missing = []
    if not checks["playcover_installed"]:
        missing.append("install PlayCover: brew install --cask playcover-community")
    if not checks["have_identity"]:
        missing.append("set up a signing identity — open PlayCover once + follow its setup wizard")
    return envelope("playcover_memory_esp",
                    {"command": "check_playcover",
                     "exit_code": 0 if not missing else 1,
                     "stdout": json.dumps(checks, indent=2),
                     "stderr": "", "duration_ms": 0},
                    missing=missing, checks=checks)


def _install_ipa(*, ipa: str | None) -> dict:
    if not ipa:
        raise ValueError("install_ipa requires --ipa path")
    if not Path(ipa).exists():
        raise FileNotFoundError(ipa)
    # PlayCover's install flow: `open -a PlayCover <IPA>` which triggers
    # the app to accept the IPA, sign it, and move it into its container.
    out = run_cmd(["open", "-a", "PlayCover", ipa], timeout=15)
    return envelope("playcover_memory_esp", out, ipa=ipa,
                     note="PlayCover window should prompt — accept the signing step.")


def _launch_app(*, bundle_id: str | None) -> dict:
    """Launch a PlayCover-installed app. Bundle ID for stock Among Us is
    `com.innersloth.amongus`; for patched builds it stays the same unless
    patch_ipa.sh renamed it."""
    if not bundle_id:
        # Pick the first .app under PlayCover's container
        if PLAYCOVER_APP_DIR.exists():
            cands = list(PLAYCOVER_APP_DIR.glob("*.app"))
            if cands:
                bundle_id = _plist_read(cands[0] / "Info.plist", "CFBundleIdentifier")
    if not bundle_id:
        raise ValueError("launch_app needs bundle_id (or an installed PlayCover app)")
    out = run_cmd(["open", "-b", bundle_id], timeout=10)
    return envelope("playcover_memory_esp", out, bundle_id=bundle_id)


def _plist_read(plist: Path, key: str) -> str | None:
    if not plist.exists():
        return None
    try:
        out = run_cmd(["/usr/libexec/PlistBuddy", "-c", f"Print {key}", str(plist)],
                      timeout=5)
        return out["stdout"].strip() or None
    except Exception:
        return None


def _push_offsets(app, *, game, offsets_path, port) -> dict:
    """Shared with ios_memory_esp — the dylib protocol is identical."""
    from . import ios_memory_esp
    return ios_memory_esp.run(app, mode="push_offsets",
                                game=game, offsets_path=offsets_path, port=port)


def _start(app, *, bundle_id, game, offsets_path, port, **extras) -> dict:
    # 1) check playcover
    c = _check()
    if c.get("exit_code") != 0:
        return c
    # 2) launch the app (if not already running)
    _launch_app(bundle_id=bundle_id)
    # Give the dylib time to install its 5-9s jittered hooks + start socket
    time.sleep(10)
    # 3) health-check the socket
    ok = _socket_ping(port)
    if not ok:
        return envelope("playcover_memory_esp",
                        {"command": "start", "exit_code": -1,
                         "stdout": "", "stderr": f"dylib not answering on 127.0.0.1:{port}",
                         "duration_ms": 0})
    # 4) push offsets
    push = _push_offsets(app, game=game, offsets_path=offsets_path, port=port)
    # 5) start memory_esp with android backend (same wire protocol)
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
    reader["push_offsets_result"] = push
    reader["bundle_id"] = bundle_id
    reader["via"] = "playcover"
    return reader


def _socket_ping(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", port))
        s.sendall(b'{"cmd":"ping"}\n')
        resp = s.recv(64)
        s.close()
        return b'"pong":1' in resp
    except Exception:
        return False


def _stop(*, pid, reader_pid, overlay_pid) -> dict:
    from . import memory_esp
    return memory_esp.run(None, mode="stop", pid=pid,
                            reader_pid=reader_pid, overlay_pid=overlay_pid)
