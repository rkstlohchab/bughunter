"""bgmi_mobile — scrcpy + adb orchestration for BGMI (and any mobile game).

Starts a scrcpy mirror from a connected Android device, hands the
mirror window off to vision_esp in capture mode, optionally pipes input
back via adb shell input or an OTG Arduino HID. Also hosts Magisk-hide
verification helpers for on-device cheat setups.
"""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path

from ._common import envelope, locate, run_cmd


def run(app, *, mode: str = "status",
        serial: str | None = None,
        pid: int | None = None,
        max_fps: int = 60,
        bit_rate: str = "20M",
        codec: str = "h265",
        project_id: str | None = None,
        model_path: str | None = None,
        overlay: bool = True,
        aim_via: str = "none",   # none | adb | arduino
        arduino_port: str | None = None,
        x: int = 0, y: int = 0,
        **_) -> dict:
    """Modes:

    - ``list_devices``          — adb devices
    - ``safety_check``          — getprop + root hide verification
    - ``start``                 — scrcpy + YOLO pipeline in background
    - ``stop``                  — kill scrcpy + vision subprocess group
    - ``status``                — scrcpy + vision log tail
    - ``send_touch``            — adb shell input tap/swipe one-shot
    """
    if mode == "list_devices":
        return _list_devices()
    if mode == "safety_check":
        return _safety_check(serial)
    if mode == "start":
        return _start(app, serial=serial, project_id=project_id,
                       max_fps=max_fps, bit_rate=bit_rate, codec=codec,
                       model_path=model_path, overlay=overlay,
                       aim_via=aim_via, arduino_port=arduino_port)
    if mode == "stop":
        return _stop(pid)
    if mode == "status":
        return _status(app, pid)
    if mode == "send_touch":
        return _send_touch(x=x, y=y, serial=serial)
    raise ValueError(f"unknown mode: {mode}")


def _list_devices() -> dict:
    adb = locate("adb", hint="brew install android-platform-tools")
    out = run_cmd([adb, "devices", "-l"], timeout=10)
    devices = []
    for line in out["stdout"].splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1],
                            "info": " ".join(parts[2:])})
    return envelope("bgmi_mobile", out, devices=devices)


def _safety_check(serial: str | None) -> dict:
    """Pull build.prop, root-visibility, SELinux state."""
    adb = locate("adb", hint="brew install android-platform-tools")
    base = [adb]
    if serial:
        base += ["-s", serial]
    probes = {
        "fingerprint": base + ["shell", "getprop", "ro.build.fingerprint"],
        "debuggable":  base + ["shell", "getprop", "ro.debuggable"],
        "secure":      base + ["shell", "getprop", "ro.secure"],
        "tags":        base + ["shell", "getprop", "ro.build.tags"],
        "type":        base + ["shell", "getprop", "ro.build.type"],
        "security_patch": base + ["shell", "getprop", "ro.build.version.security_patch"],
        "sdk":         base + ["shell", "getprop", "ro.build.version.sdk"],
        "model":       base + ["shell", "getprop", "ro.product.model"],
    }
    results = {}
    for name, cmd in probes.items():
        out = run_cmd(cmd, timeout=5)
        results[name] = (out["stdout"] or "").strip()
    # root visibility: which su, mountinfo system tampering, zygisk DenyList
    out_su = run_cmd(base + ["shell", "which", "su"], timeout=5)
    results["su_path"] = (out_su["stdout"] or "").strip()
    out_magisk = run_cmd(base + ["shell", "pm", "path", "com.topjohnwu.magisk"],
                          timeout=5)
    results["magisk_pkg_visible"] = (out_magisk["stdout"] or "").strip()
    out_selinux = run_cmd(base + ["shell", "getenforce"], timeout=5)
    results["selinux"] = (out_selinux["stdout"] or "").strip()

    # pass/fail
    flags = []
    if results.get("debuggable") != "0":
        flags.append("ro.debuggable != 0")
    if results.get("secure") != "1":
        flags.append("ro.secure != 1")
    if results.get("tags") != "release-keys":
        flags.append(f"ro.build.tags={results.get('tags')}")
    if results.get("type") != "user":
        flags.append(f"ro.build.type={results.get('type')}")
    if results.get("selinux", "").lower() != "enforcing":
        flags.append(f"selinux={results.get('selinux')}")
    if results.get("su_path"):
        flags.append("su binary visible — add BGMI package to Shamiko DenyList")
    if results.get("magisk_pkg_visible"):
        flags.append("Magisk package visible to BGMI")
    return envelope("bgmi_mobile",
                    {"command": "safety_check", "exit_code": 0 if not flags else 1,
                     "stdout": json.dumps(results, indent=2),
                     "stderr": "", "duration_ms": 0},
                    results=results, flags=flags, ok=not flags)


def _start(app, *, serial, project_id, max_fps, bit_rate, codec,
           model_path, overlay, aim_via, arduino_port) -> dict:
    scrcpy = locate("scrcpy", hint="brew install scrcpy")
    adb = locate("adb", hint="brew install android-platform-tools")

    # scrcpy writes to v4l2 on Linux for pipeline-friendly capture;
    # on macOS we rely on OBS Virtual Camera or scrcpy's recording.
    log_dir = Path(app.store.root) / "gamelab_logs" / "bgmi_mobile"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = int(time.time())
    scrcpy_log = log_dir / f"scrcpy-{stamp}.log"
    vision_log = log_dir / f"vision-{stamp}.log"

    scrcpy_cmd = [scrcpy, "--max-fps", str(max_fps),
                  "--video-codec", codec,
                  "--video-bit-rate", bit_rate,
                  "--no-audio",
                  "--window-title", f"GameLab-BGMI-{stamp}"]
    if serial:
        scrcpy_cmd += ["--serial", serial]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    sf = open(scrcpy_log, "ab", buffering=0)
    scrcpy_proc = subprocess.Popen(scrcpy_cmd, stdout=sf, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)

    repo_root = Path(__file__).resolve().parents[3]
    vision_script = repo_root / "cheats" / "mobile-bgmi" / "bgmi_esp.py"
    if not vision_script.exists():
        return envelope("bgmi_mobile",
                        {"command": " ".join(scrcpy_cmd),
                         "exit_code": None, "stdout": "",
                         "stderr": "bgmi_esp.py missing",
                         "duration_ms": 0},
                        scrcpy_pid=scrcpy_proc.pid, pid=scrcpy_proc.pid)
    vision_cfg = {
        "project_id": project_id,
        "model_path": model_path or str(repo_root / "cheats" / "mobile-bgmi" / "weights" / "bgmi_v8n.pt"),
        "window_title": f"GameLab-BGMI-{stamp}",
        "overlay": overlay,
        "aim_via": aim_via,
        "arduino_port": arduino_port,
        "serial": serial,
    }
    vf = open(vision_log, "ab", buffering=0)
    vision_cmd = ["python3", str(vision_script),
                  "--config-json", json.dumps(vision_cfg)]
    vision_proc = subprocess.Popen(vision_cmd, stdout=vf, stderr=subprocess.STDOUT,
                                   env=env, cwd=str(vision_script.parent),
                                   start_new_session=True)

    # track the vision pid as primary (stop stops both)
    (log_dir / f"group-{stamp}.json").write_text(json.dumps({
        "scrcpy_pid": scrcpy_proc.pid,
        "vision_pid": vision_proc.pid,
        "scrcpy_log": str(scrcpy_log),
        "vision_log": str(vision_log),
    }), encoding="utf-8")

    return envelope("bgmi_mobile",
                    {"command": " && ".join([" ".join(scrcpy_cmd),
                                              " ".join(vision_cmd)]),
                     "exit_code": None, "stdout": "",
                     "stderr": "", "duration_ms": 0},
                    pid=vision_proc.pid,
                    scrcpy_pid=scrcpy_proc.pid,
                    vision_pid=vision_proc.pid,
                    scrcpy_log=str(scrcpy_log),
                    vision_log=str(vision_log),
                    mode="start")


def _stop(pid: int | None) -> dict:
    if not pid:
        raise ValueError("stop requires pid")
    # try to stop both scrcpy and vision — find the matching group file
    for _dir in Path(".").iterdir():
        pass
    # Fallback: just SIGTERM the group we were given + known scrcpy
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
            time.sleep(0.3)
        except ProcessLookupError:
            break
    # also sweep scrcpy
    subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
    return envelope("bgmi_mobile",
                    {"command": f"kill {pid}", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=pid, mode="stop")


def _status(app, pid: int | None) -> dict:
    if not pid:
        raise ValueError("status requires pid")
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    log_dir = Path(app.store.root) / "gamelab_logs" / "bgmi_mobile"
    tail = ""
    if log_dir.exists():
        logs = sorted(log_dir.glob("vision-*.log"))
        if logs:
            tail = logs[-1].read_bytes()[-4000:].decode("utf-8", errors="replace")
    return envelope("bgmi_mobile",
                    {"command": "", "exit_code": 0 if alive else -1,
                     "stdout": tail, "stderr": "", "duration_ms": 0},
                    pid=pid, alive=alive)


def _send_touch(*, x: int, y: int, serial: str | None) -> dict:
    adb = locate("adb")
    base = [adb]
    if serial:
        base += ["-s", serial]
    return envelope("bgmi_mobile",
                    run_cmd(base + ["shell", "input", "tap", str(x), str(y)],
                            timeout=5),
                    x=x, y=y)
