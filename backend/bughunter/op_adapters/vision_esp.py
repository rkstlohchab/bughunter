"""vision_esp adapter — capture-card + YOLO + optional HID output.

Orchestrates a long-running vision pipeline as a subprocess. The actual
pipeline is ``cheats/external-vision/vision_aim.py`` — this adapter
spawns, configures, pauses, resumes, and stops it. Also supports a
``capture_dataset`` mode for building training sets.

The adapter never ingests game memory. All data flows live outside
Python: subprocess stdout log is tail-buffered, model weights path is
passed in. Keeps the operator stack's "no cross-plumbing" rule happy.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ._common import envelope


def run(app, *, mode: str = "start",
        pid: int | None = None,
        project_id: str | None = None,
        capture_index: int = 0,
        capture_w: int = 1920,
        capture_h: int = 1080,
        fov: int = 320,
        arduino_port: str | None = None,
        arduino_baud: int = 115200,
        model_path: str | None = None,
        confidence: float = 0.55,
        head_offset: float = 0.22,
        aim_smooth: float = 0.35,
        tick_ms: int = 90,
        miss_rate: float = 0.08,
        reaction_floor_ms: int = 180,
        sens_scale: float = 2.6,
        esp_overlay: bool = True,
        aimbot_enabled: bool = False,
        trigger_enabled: bool = False,
        source: str | None = None,
        duration_s: int = 300,
        fps: int = 5,
        **_) -> dict:
    """Modes:

    - ``start``           — spawn vision_aim.py as background subprocess
    - ``stop``            — SIGTERM the pid
    - ``status``          — stat the pid and read tail
    - ``capture_dataset`` — run train_data_capture.py to extract labeled-ready frames
    """
    if mode == "start":
        return _start(app, project_id=project_id,
                      capture_index=capture_index,
                      capture_w=capture_w, capture_h=capture_h, fov=fov,
                      arduino_port=arduino_port, arduino_baud=arduino_baud,
                      model_path=model_path, confidence=confidence,
                      head_offset=head_offset, aim_smooth=aim_smooth,
                      tick_ms=tick_ms, miss_rate=miss_rate,
                      reaction_floor_ms=reaction_floor_ms,
                      sens_scale=sens_scale,
                      esp_overlay=esp_overlay,
                      aimbot_enabled=aimbot_enabled,
                      trigger_enabled=trigger_enabled)
    if mode == "stop":
        return _stop(pid)
    if mode == "status":
        return _status(app, pid)
    if mode == "capture_dataset":
        if not source:
            raise ValueError("capture_dataset requires source (capture index, video path, or scrcpy URL)")
        return _capture_dataset(app, project_id=project_id,
                                source=source, duration_s=duration_s,
                                fps=fps)
    raise ValueError(f"unknown mode: {mode}")


def _start(app, *, project_id, capture_index, capture_w, capture_h, fov,
           arduino_port, arduino_baud, model_path, confidence,
           head_offset, aim_smooth, tick_ms, miss_rate,
           reaction_floor_ms, sens_scale,
           esp_overlay, aimbot_enabled, trigger_enabled) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "cheats" / "external-vision" / "vision_aim.py"
    if not script.exists():
        raise FileNotFoundError(f"vision_aim.py not found at {script}")
    if not model_path:
        # choose default weights by project
        weights_dir = repo_root / "cheats" / "external-vision" / "weights"
        default_name = _default_weights_for_project(app, project_id)
        guess = weights_dir / default_name
        if not guess.exists():
            # fall back to any .pt
            found = list(weights_dir.glob("*.pt"))
            if not found:
                raise FileNotFoundError(f"no model weights in {weights_dir} — "
                                         "train one first with capture_dataset "
                                         "+ kaggle.launch_training")
            guess = found[0]
        model_path = str(guess)

    log_dir = Path(app.store.root) / "gamelab_logs" / "vision_esp"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = int(time.time())
    log_path = log_dir / f"run-{stamp}.log"

    cfg = {
        "capture_index": capture_index, "capture_w": capture_w,
        "capture_h": capture_h, "fov": fov,
        "arduino_port": arduino_port, "arduino_baud": arduino_baud,
        "model_path": model_path, "confidence": confidence,
        "head_offset": head_offset, "aim_smooth": aim_smooth,
        "tick_ms": tick_ms, "miss_rate": miss_rate,
        "reaction_floor_ms": reaction_floor_ms, "sens_scale": sens_scale,
        "esp_overlay": esp_overlay, "aimbot_enabled": aimbot_enabled,
        "trigger_enabled": trigger_enabled,
        "project_id": project_id,
    }

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = ["python3", str(script), "--config-json", json.dumps(cfg)]
    f = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                            env=env, cwd=str(script.parent),
                            start_new_session=True)
    return envelope("vision_esp",
                    {"command": " ".join(cmd), "exit_code": None,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=proc.pid, log_path=str(log_path),
                    model_path=model_path, mode="start")


def _stop(pid: int | None) -> dict:
    if not pid:
        raise ValueError("stop requires pid")
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return envelope("vision_esp", {"command": f"kill {pid}", "exit_code": 0,
                                       "stdout": "already dead", "stderr": "",
                                       "duration_ms": 0}, pid=pid)
    time.sleep(0.5)
    try:
        os.killpg(os.getpgid(pid), 0)   # still alive?
        # force
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    return envelope("vision_esp", {"command": f"kill {pid}", "exit_code": 0,
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
    tail = ""
    log_dir = Path(app.store.root) / "gamelab_logs" / "vision_esp"
    if log_dir.exists():
        logs = sorted(log_dir.glob("run-*.log"))
        if logs:
            text = logs[-1].read_bytes()[-4000:].decode("utf-8", errors="replace")
            tail = text
    return envelope("vision_esp",
                    {"command": "", "exit_code": 0 if alive else -1,
                     "stdout": tail, "stderr": "", "duration_ms": 0},
                    pid=pid, alive=alive)


def _capture_dataset(app, *, project_id, source, duration_s, fps) -> dict:
    """Extract frames at a given fps from a video source into a project
    dataset folder, ready for Roboflow / CVAT labeling."""
    out_root = Path(app.store.root) / "gamelab_datasets" / (project_id or "unassigned")
    stamp = int(time.time())
    out_dir = out_root / f"capture-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # source can be: an integer camera index, a file path, or a scrcpy device serial
    try:
        src_arg = int(source)
    except ValueError:
        src_arg = source
    import importlib.util
    if importlib.util.find_spec("cv2") is None:
        raise RuntimeError("opencv-python required for capture_dataset")
    import cv2
    cap = cv2.VideoCapture(src_arg)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video source: {source}")
    period = 1.0 / max(fps, 1)
    start = time.monotonic()
    saved = 0
    last_save = 0.0
    try:
        while time.monotonic() - start < duration_s:
            ok, frame = cap.read()
            if not ok:
                continue
            now = time.monotonic()
            if now - last_save >= period:
                fname = out_dir / f"frame-{saved:06d}.jpg"
                cv2.imwrite(str(fname), frame)
                saved += 1
                last_save = now
    finally:
        cap.release()
    return envelope("vision_esp",
                    {"command": "capture_dataset", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": duration_s * 1000},
                    frames=saved, out_dir=str(out_dir),
                    fps=fps, duration_s=duration_s)


def _default_weights_for_project(app, project_id: str | None) -> str:
    if not project_id:
        return "generic.pt"
    proj = app.store.get("cheat_project", project_id)
    if not proj:
        return "generic.pt"
    game = proj.get("game", "")
    return {"valorant": "valorant_v8n.pt", "cs2": "cs2_v8n.pt",
            "bgmi": "bgmi_v8n.pt", "cod_mw": "codmw_v8n.pt",
            "apex": "apex_v8n.pt", "rust": "rust_v8n.pt",
            "fortnite": "fortnite_v8n.pt"}.get(game, "generic.pt")
