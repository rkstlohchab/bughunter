#!/usr/bin/env python3
# language: Python 3.11+, file: vision_aim.py, target: capture-host (Linux/macOS/Windows)
# External vision aimbot + ESP. Reads HDMI capture of the gaming PC, sends
# mouse deltas to Arduino HID (or KMBox). Zero software footprint on the gaming PC.
#
# Called by the vision_esp adapter with --config-json '<json>'. Also runs
# standalone — see python3 vision_aim.py --help.

from __future__ import annotations

import argparse
import json
import signal
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np


# -------------------------- config ---------------------------------------

@dataclass
class Config:
    capture_index: int = 0
    capture_w: int = 1920
    capture_h: int = 1080
    fov: int = 320
    arduino_port: Optional[str] = None
    arduino_baud: int = 115200
    kmbox_endpoint: Optional[str] = None   # host:port
    model_path: str = "weights/generic_v8n.pt"
    confidence: float = 0.55
    head_offset: float = 0.22
    aim_smooth: float = 0.35
    tick_ms: int = 90
    miss_rate: float = 0.08
    reaction_floor_ms: int = 180
    sens_scale: float = 2.6
    esp_overlay: bool = True
    aimbot_enabled: bool = False
    trigger_enabled: bool = False
    # ESP drawing
    overlay_window: str = "GameLab ESP"
    draw_box: bool = True
    draw_name: bool = False    # vision-only can't name — leave off
    draw_hp: bool = False
    # Flags
    project_id: Optional[str] = None
    log_detections: bool = False


# -------------------------- HID targets ---------------------------------

class HidBase:
    def move(self, dx: int, dy: int): raise NotImplementedError
    def click(self): raise NotImplementedError
    def close(self): pass


class ArduinoHid(HidBase):
    """Serial protocol: 0xAA | i16 dx | i16 dy | 0x55  (move)
                        0xBB | 0x55                   (click)
                        0xCC | 0x55                   (ping)   -> 0xCD | 0x55
    """
    def __init__(self, port: str, baud: int):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.5)
        time.sleep(1.8)  # Leonardo reset on open
        self.ser.reset_input_buffer()

    def move(self, dx: int, dy: int):
        dx = max(-32767, min(32767, dx))
        dy = max(-32767, min(32767, dy))
        self.ser.write(struct.pack("<BhhB", 0xAA, dx, dy, 0x55))

    def click(self):
        self.ser.write(struct.pack("<BB", 0xBB, 0x55))

    def close(self):
        try: self.ser.close()
        except Exception: pass


class KmboxNet(HidBase):
    """KMBox B+ / Pro over TCP JSON on port 65432."""
    def __init__(self, endpoint: str):
        import socket
        host, port = endpoint.split(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(1.5)
        self.sock.connect((host, int(port)))

    def _send(self, obj: dict):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def move(self, dx: int, dy: int):
        self._send({"cmd": "mouse_move", "dx": int(dx), "dy": int(dy)})

    def click(self):
        self._send({"cmd": "mouse_click", "btn": "left"})

    def close(self):
        try: self.sock.close()
        except Exception: pass


class NullHid(HidBase):
    def move(self, dx, dy): pass
    def click(self): pass


# -------------------------- detection -----------------------------------

@dataclass
class Target:
    cx: float
    cy: float
    conf: float
    w: float
    h: float


def pick_target(dets: list[Target], cx: float, cy: float) -> Optional[Target]:
    best, best_score = None, -1.0
    for d in dets:
        dist = ((d.cx - cx) ** 2 + (d.cy - cy) ** 2) ** 0.5
        score = d.conf / (1.0 + dist / 100.0)
        if score > best_score:
            best, best_score = d, score
    return best


def bezier_step(dx: float, dy: float, t: float) -> tuple[float, float]:
    ease = t * t * (3.0 - 2.0 * t)
    return dx * ease, dy * ease


# -------------------------- main loop -----------------------------------

_stop = False


def _handle_sig(_sig, _frm):
    global _stop
    _stop = True


def main(cfg: Config) -> int:
    from ultralytics import YOLO
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    cap = cv2.VideoCapture(cfg.capture_index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.capture_w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.capture_h)
    cap.set(cv2.CAP_PROP_FPS, 60)
    if not cap.isOpened():
        print(f"[vision_aim] failed to open capture index {cfg.capture_index}", flush=True)
        return 2

    model = YOLO(cfg.model_path)

    hid: HidBase
    if cfg.aimbot_enabled:
        if cfg.kmbox_endpoint:
            hid = KmboxNet(cfg.kmbox_endpoint)
        elif cfg.arduino_port:
            hid = ArduinoHid(cfg.arduino_port, cfg.arduino_baud)
        else:
            print("[vision_aim] aimbot enabled but no HID target — using NullHid", flush=True)
            hid = NullHid()
    else:
        hid = NullHid()

    rng = np.random.default_rng()
    cx0, cy0 = cfg.capture_w // 2, cfg.capture_h // 2
    x1 = cx0 - cfg.fov // 2
    y1 = cy0 - cfg.fov // 2
    fov_cx = cfg.fov / 2.0
    fov_cy = cfg.fov / 2.0

    last_tick = 0.0
    last_acquire = 0.0
    reaction_due = 0.0
    overlay_name = cfg.overlay_window

    if cfg.esp_overlay:
        cv2.namedWindow(overlay_name, cv2.WINDOW_NORMAL)

    print(f"[vision_aim] running cfg={json.dumps(cfg.__dict__, default=str)}", flush=True)

    while not _stop:
        ok, frame = cap.read()
        if not ok:
            continue
        roi = frame[y1:y1 + cfg.fov, x1:x1 + cfg.fov]
        results = model.predict(roi, conf=cfg.confidence,
                                 verbose=False, imgsz=320)
        dets: list[Target] = []
        for r in results:
            for box in r.boxes:
                x1b, y1b, x2b, y2b = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cx = (x1b + x2b) / 2.0
                cy = y1b + (y2b - y1b) * cfg.head_offset
                dets.append(Target(cx, cy, conf, x2b - x1b, y2b - y1b))

        t = pick_target(dets, fov_cx, fov_cy) if dets else None

        # ESP overlay
        if cfg.esp_overlay:
            disp = roi.copy()
            cx_c = int(fov_cx)
            cy_c = int(fov_cy)
            cv2.line(disp, (cx_c - 10, cy_c), (cx_c + 10, cy_c), (0, 255, 0), 1)
            cv2.line(disp, (cx_c, cy_c - 10), (cx_c, cy_c + 10), (0, 255, 0), 1)
            for d in dets:
                color = (0, 0, 255) if d is t else (0, 200, 255)
                x1b = int(d.cx - d.w / 2); y1b = int(d.cy - d.h * cfg.head_offset)
                x2b = int(d.cx + d.w / 2); y2b = int(y1b + d.h)
                cv2.rectangle(disp, (x1b, y1b), (x2b, y2b), color, 2)
                cv2.circle(disp, (int(d.cx), int(d.cy)), 4, color, -1)
                cv2.putText(disp, f"{d.conf:.2f}", (x1b, max(0, y1b - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            cv2.imshow(overlay_name, disp)
            cv2.waitKey(1)

        if not cfg.aimbot_enabled:
            continue

        now = time.monotonic()

        # target acquisition / reaction floor
        if t is not None:
            if now - last_acquire > 0.6:    # brand-new target
                reaction_due = now + max(cfg.reaction_floor_ms / 1000.0,
                                         rng.lognormal(mean=np.log(0.22), sigma=0.28))
            last_acquire = now
            if now < reaction_due:
                continue
        else:
            continue

        dx = (t.cx - fov_cx) * cfg.sens_scale
        dy = (t.cy - fov_cy) * cfg.sens_scale

        # dead zone
        if abs(dx) < 3 and abs(dy) < 3:
            if cfg.trigger_enabled and t.conf > 0.72:
                hid.click()
            continue

        # humanize
        dx, dy = bezier_step(dx, dy, 1.0 - cfg.aim_smooth)
        if rng.random() < cfg.miss_rate:
            dx += rng.uniform(-14.0, 14.0)
            dy += rng.uniform(-10.0, 10.0)

        # tick gate
        if (now - last_tick) * 1000.0 < cfg.tick_ms:
            continue
        last_tick = now

        hid.move(int(round(dx)), int(round(dy)))
        if cfg.log_detections:
            print(f"[move] dx={int(dx)} dy={int(dy)} conf={t.conf:.2f}", flush=True)

    cap.release()
    hid.close()
    if cfg.esp_overlay:
        cv2.destroyAllWindows()
    return 0


def _parse_argv(argv: list[str]) -> Config:
    ap = argparse.ArgumentParser(description="External vision aimbot + ESP")
    ap.add_argument("--config-json", type=str,
                    help="JSON blob of config (overrides every other flag)")
    ap.add_argument("--capture-index", type=int, default=0)
    ap.add_argument("--arduino-port", type=str, default=None)
    ap.add_argument("--kmbox", type=str, default=None,
                    help="KMBox endpoint host:port")
    ap.add_argument("--model", type=str, default="weights/generic_v8n.pt")
    ap.add_argument("--aimbot", action="store_true")
    ap.add_argument("--trigger", action="store_true")
    ap.add_argument("--no-esp", action="store_true")
    ns = ap.parse_args(argv)
    if ns.config_json:
        data = json.loads(ns.config_json)
        return Config(**{k: v for k, v in data.items()
                         if k in Config.__dataclass_fields__})
    cfg = Config()
    cfg.capture_index = ns.capture_index
    cfg.arduino_port = ns.arduino_port
    cfg.kmbox_endpoint = ns.kmbox
    cfg.model_path = ns.model
    cfg.aimbot_enabled = ns.aimbot
    cfg.trigger_enabled = ns.trigger
    cfg.esp_overlay = not ns.no_esp
    return cfg


if __name__ == "__main__":
    sys.exit(main(_parse_argv(sys.argv[1:])))
