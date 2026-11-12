#!/usr/bin/env python3
# language: Python 3.11+, file: bgmi_esp.py, target: PC host running scrcpy
# BGMI (and any mobile game) ESP + optional aim via scrcpy mirror.
#
# Pipeline:
#   phone  --(USB)-->  adb/scrcpy on PC  --(window capture)-->  this script
#   this script runs YOLO on the mirror, draws overlay in a PyQt/OpenCV window,
#   optionally sends touch events back via adb shell input, or mouse events
#   via Arduino HID if the phone has OTG mouse support.
#
# Called by bgmi_mobile adapter with --config-json '<json>'. Runs standalone
# with --window-title <scrcpy_window_title>.

from __future__ import annotations

import argparse
import json
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class Config:
    project_id: Optional[str] = None
    model_path: str = "weights/bgmi_v8n.pt"
    window_title: str = "GameLab-BGMI"
    overlay: bool = True
    aim_via: str = "none"               # none | adb | arduino
    adb_serial: Optional[str] = None
    arduino_port: Optional[str] = None
    arduino_baud: int = 115200
    confidence: float = 0.45
    head_offset: float = 0.18
    aim_tick_ms: int = 150
    miss_rate: float = 0.12
    reaction_floor_ms: int = 220
    smoothing: float = 0.45
    draw_boxes: bool = True
    draw_centers: bool = True
    log_detections: bool = False


_stop = False


def _handle_sig(_s, _f):
    global _stop
    _stop = True


class WindowCapture:
    """Cross-platform grab of a scrcpy window by title. Linux: xdotool +
    xwd. macOS: screencapture by display, filter via window cropping.
    Windows: pygetwindow + mss. Falls back to full-screen if window not
    found."""

    def __init__(self, title: str):
        self.title = title
        import platform
        self.platform = platform.system()
        self._mss = None
        if self.platform == "Windows":
            try:
                import mss
                self._mss = mss.mss()
            except Exception:
                self._mss = None
        self.region = None

    def _locate(self) -> tuple[int, int, int, int] | None:
        if self.platform == "Linux":
            r = subprocess.run(["xdotool", "search", "--name", self.title],
                                capture_output=True, text=True, timeout=1)
            wids = [w for w in r.stdout.splitlines() if w.strip()]
            if not wids:
                return None
            wid = wids[0]
            g = subprocess.run(["xdotool", "getwindowgeometry", wid],
                                capture_output=True, text=True, timeout=1).stdout
            pos_m = None
            size_m = None
            for line in g.splitlines():
                line = line.strip()
                if line.startswith("Position:"):
                    pos_m = line.split("Position:", 1)[1].split("(")[0].strip()
                if line.startswith("Geometry:"):
                    size_m = line.split("Geometry:", 1)[1].strip()
            if not pos_m or not size_m:
                return None
            x, y = [int(v) for v in pos_m.split(",")]
            w, h = [int(v) for v in size_m.split("x")]
            return x, y, w, h
        if self.platform == "Windows":
            try:
                import pygetwindow as gw
                wins = [w for w in gw.getAllWindows()
                        if self.title in (w.title or "")]
                if not wins:
                    return None
                w = wins[0]
                return w.left, w.top, w.width, w.height
            except Exception:
                return None
        # macOS: no reliable per-window capture via std-lib — use full screen
        return None

    def grab(self) -> Optional[np.ndarray]:
        import platform
        if self.region is None:
            self.region = self._locate()
        if self.platform == "Windows" and self._mss and self.region:
            x, y, w, h = self.region
            mon = {"left": x, "top": y, "width": w, "height": h}
            img = np.array(self._mss.grab(mon))
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if self.platform == "Linux" and self.region:
            x, y, w, h = self.region
            # ffmpeg one-shot grab (fast and already present with scrcpy)
            cmd = ["ffmpeg", "-y", "-f", "x11grab",
                   "-video_size", f"{w}x{h}",
                   "-i", f":0.0+{x},{y}", "-frames:v", "1",
                   "-f", "image2pipe", "-vcodec", "bmp", "-"]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=1)
                arr = np.frombuffer(r.stdout, dtype=np.uint8)
                if arr.size == 0:
                    return None
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                return img
            except Exception:
                return None
        # fall-back: use default video capture (OBS Virtual Cam shows scrcpy)
        if not hasattr(self, "_cap"):
            self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            return None
        ok, frame = self._cap.read()
        return frame if ok else None


class AdbTouch:
    def __init__(self, serial: Optional[str]):
        self.serial = serial
        self._base = ["adb"] + (["-s", serial] if serial else [])
    def tap(self, x: int, y: int):
        subprocess.run(self._base + ["shell", "input", "tap",
                                      str(int(x)), str(int(y))],
                       capture_output=True)


class ArduinoMouse:
    def __init__(self, port: str, baud: int):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.5)
        time.sleep(1.8)
    def move(self, dx: int, dy: int):
        dx = max(-32767, min(32767, int(dx)))
        dy = max(-32767, min(32767, int(dy)))
        self.ser.write(struct.pack("<BhhB", 0xAA, dx, dy, 0x55))
    def click(self):
        self.ser.write(struct.pack("<BB", 0xBB, 0x55))


def pick_target(dets, cx, cy):
    best, best_score = None, -1.0
    for d in dets:
        dist = ((d["cx"] - cx) ** 2 + (d["cy"] - cy) ** 2) ** 0.5
        score = d["conf"] / (1.0 + dist / 100.0)
        if score > best_score:
            best, best_score = d, score
    return best


def main(cfg: Config) -> int:
    from ultralytics import YOLO
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)
    model = YOLO(cfg.model_path)
    cap = WindowCapture(cfg.window_title)

    touch = adb = arduino = None
    if cfg.aim_via == "adb":
        adb = AdbTouch(cfg.adb_serial)
    elif cfg.aim_via == "arduino" and cfg.arduino_port:
        arduino = ArduinoMouse(cfg.arduino_port, cfg.arduino_baud)

    rng = np.random.default_rng()
    last_tick = 0.0
    reaction_due = 0.0
    last_acquire = 0.0
    win = f"{cfg.window_title} — overlay"
    if cfg.overlay:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    print(f"[bgmi_esp] running cfg={json.dumps(cfg.__dict__, default=str)}", flush=True)

    while not _stop:
        frame = cap.grab()
        if frame is None:
            time.sleep(0.05)
            continue
        h, w = frame.shape[:2]
        results = model.predict(frame, conf=cfg.confidence,
                                 verbose=False, imgsz=640)
        dets = []
        for r in results:
            for box in r.boxes:
                x1b, y1b, x2b, y2b = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cx = (x1b + x2b) / 2.0
                cy = y1b + (y2b - y1b) * cfg.head_offset
                dets.append({"cx": cx, "cy": cy, "conf": conf,
                             "w": x2b - x1b, "h": y2b - y1b,
                             "x1": x1b, "y1": y1b, "x2": x2b, "y2": y2b})

        center_x, center_y = w / 2.0, h / 2.0
        target = pick_target(dets, center_x, center_y) if dets else None

        if cfg.overlay:
            disp = frame.copy()
            cv2.line(disp, (int(center_x) - 10, int(center_y)),
                     (int(center_x) + 10, int(center_y)), (0, 255, 0), 1)
            cv2.line(disp, (int(center_x), int(center_y) - 10),
                     (int(center_x), int(center_y) + 10), (0, 255, 0), 1)
            for d in dets:
                color = (0, 0, 255) if d is target else (0, 200, 255)
                if cfg.draw_boxes:
                    cv2.rectangle(disp,
                                  (int(d["x1"]), int(d["y1"])),
                                  (int(d["x2"]), int(d["y2"])), color, 2)
                if cfg.draw_centers:
                    cv2.circle(disp, (int(d["cx"]), int(d["cy"])), 4, color, -1)
                cv2.putText(disp, f"{d['conf']:.2f}",
                            (int(d["x1"]), max(0, int(d["y1"]) - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            cv2.imshow(win, disp)
            cv2.waitKey(1)

        if cfg.aim_via == "none" or target is None:
            continue

        now = time.monotonic()
        if now - last_acquire > 0.7:
            reaction_due = now + max(cfg.reaction_floor_ms / 1000.0,
                                     rng.lognormal(mean=np.log(0.26), sigma=0.28))
        last_acquire = now
        if now < reaction_due:
            continue
        if (now - last_tick) * 1000.0 < cfg.aim_tick_ms:
            continue
        last_tick = now

        if cfg.aim_via == "adb" and adb:
            if rng.random() < cfg.miss_rate:
                adb.tap(int(target["cx"] + rng.uniform(-30, 30)),
                        int(target["cy"] + rng.uniform(-20, 20)))
            else:
                adb.tap(int(target["cx"]), int(target["cy"]))
        elif cfg.aim_via == "arduino" and arduino:
            dx = (target["cx"] - center_x) * (1.0 - cfg.smoothing)
            dy = (target["cy"] - center_y) * (1.0 - cfg.smoothing)
            if rng.random() < cfg.miss_rate:
                dx += rng.uniform(-14.0, 14.0)
                dy += rng.uniform(-10.0, 10.0)
            arduino.move(int(round(dx)), int(round(dy)))

        if cfg.log_detections:
            print(f"[detect] n={len(dets)} best={target['conf']:.2f}", flush=True)

    if cfg.overlay:
        cv2.destroyAllWindows()
    return 0


def _parse_argv(argv: list[str]) -> Config:
    ap = argparse.ArgumentParser(description="BGMI ESP via scrcpy")
    ap.add_argument("--config-json", type=str)
    ap.add_argument("--window-title", default="GameLab-BGMI")
    ap.add_argument("--model", default="weights/bgmi_v8n.pt")
    ap.add_argument("--aim-via", default="none",
                    choices=["none", "adb", "arduino"])
    ap.add_argument("--serial", default=None)
    ap.add_argument("--arduino-port", default=None)
    ap.add_argument("--no-overlay", action="store_true")
    ns = ap.parse_args(argv)
    if ns.config_json:
        data = json.loads(ns.config_json)
        return Config(**{k: v for k, v in data.items()
                         if k in Config.__dataclass_fields__})
    cfg = Config()
    cfg.window_title = ns.window_title
    cfg.model_path = ns.model
    cfg.aim_via = ns.aim_via
    cfg.adb_serial = ns.serial
    cfg.arduino_port = ns.arduino_port
    cfg.overlay = not ns.no_overlay
    return cfg


if __name__ == "__main__":
    sys.exit(main(_parse_argv(sys.argv[1:])))
