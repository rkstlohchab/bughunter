#!/usr/bin/env python3
# language: Python 3.11+, file: combat_loop.py
# GameLab combat loop — consumes the universal Frame, picks the best
# target, computes a humanized aim delta, sends it over KMBox Net OR
# Arduino HID OR (mobile) ADB input.
#
# Humanization: bezier curve, reaction-time floor, miss-rate, dead zone,
# tick gate, spray recoil-transfer cap. See prompts/game-knowledge/opsec/
# behavioral.md.
#
# This process ONLY sends inputs. It does not write game memory. No-recoil
# via memory write is a separate module (combat_recoil_writer.py) that
# requires an authorized write primitive and is intentionally not spawned
# automatically — operator approves per game.

from __future__ import annotations

import argparse
import json
import math
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class CombatConfig:
    input_path: str = "/tmp/gamelab_frame.json"
    aim_target: str = "head"               # head | neck | pelvis | center
    aim_key: str = ""                      # "" = always; else poll real device via arduino "button" line (skipped if "")
    hid_backend: str = "arduino"           # arduino | kmbox | adb | null
    arduino_port: Optional[str] = None
    arduino_baud: int = 115200
    kmbox_endpoint: Optional[str] = None   # host:port
    adb_serial: Optional[str] = None
    fov_px: float = 180.0                  # only lock inside this radius from crosshair
    sens_scale: float = 2.6
    aim_smooth: float = 0.42
    tick_ms: int = 90
    reaction_floor_ms: int = 180
    miss_rate: float = 0.08
    deadzone_px: int = 3
    spray_transfer: float = 0.0            # 0 = no recoil correction (safe default)
    trigger_enabled: bool = False
    trigger_fov_px: float = 8.0
    trigger_conf: int = 1                  # entity hp>0 required; stricter via extra flags
    local_team: int = -1                   # overridden by frame


class ArduinoHid:
    def __init__(self, port: str, baud: int):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.5)
        time.sleep(1.8); self.ser.reset_input_buffer()
    def move(self, dx, dy):
        self.ser.write(struct.pack("<BhhB", 0xAA, int(dx), int(dy), 0x55))
    def click(self): self.ser.write(struct.pack("<BB", 0xBB, 0x55))
    def close(self):
        try: self.ser.close()
        except Exception: pass


class KmboxHid:
    def __init__(self, endpoint: str):
        host, port = endpoint.split(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(1.0); self.sock.connect((host, int(port)))
    def move(self, dx, dy):
        self.sock.sendall((json.dumps({"cmd": "mouse_move",
                                        "dx": int(dx), "dy": int(dy)}) + "\n").encode())
    def click(self):
        self.sock.sendall(b'{"cmd":"mouse_click","btn":"left"}\n')
    def close(self):
        try: self.sock.close()
        except Exception: pass


class AdbHid:
    """Fallback: ADB tap at absolute screen coords — latency ~60-120ms."""
    def __init__(self, serial: Optional[str]):
        import subprocess
        self.serial = serial
        self.sp = subprocess
        self.base = ["adb"] + (["-s", serial] if serial else [])
    def move(self, dx, dy):
        # ADB mouse injection requires a specific implementation per device;
        # real deployments use sendevent. For taps use .tap(x, y).
        pass
    def click(self): pass
    def tap(self, x, y):
        self.sp.run(self.base + ["shell", "input", "tap", str(int(x)), str(int(y))],
                     capture_output=True)
    def close(self): pass


class NullHid:
    def move(self, *_): pass
    def click(self): pass
    def close(self): pass


def world_to_screen(world, mat, w, h):
    x, y, z = world
    cx = mat[0]*x + mat[1]*y + mat[2]*z + mat[3]
    cy = mat[4]*x + mat[5]*y + mat[6]*z + mat[7]
    cw = mat[12]*x + mat[13]*y + mat[14]*z + mat[15]
    if cw < 0.01: return None
    return ((1.0 + cx / cw) * 0.5 * w, (1.0 - cy / cw) * 0.5 * h)


def pick_target(frame: dict, cfg: CombatConfig, cx: float, cy: float) -> Optional[tuple[float, float, dict]]:
    local_team = frame.get("local_team", -1)
    mat = frame.get("view", {}).get("data", [0.0] * 16)
    w = frame.get("viewport", {}).get("width", 1920)
    h = frame.get("viewport", {}).get("height", 1080)
    best, best_score = None, -1.0
    for e in frame.get("entities", []):
        if local_team == e.get("team") or e.get("hp", 0) <= 0:
            continue
        key = {"head": "head_world", "neck": "head_world",
               "pelvis": "feet_world", "center": "center_world"}[cfg.aim_target]
        s = world_to_screen(tuple(e.get(key, (0, 0, 0))), mat, w, h)
        if s is None: continue
        dx = s[0] - cx; dy = s[1] - cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > cfg.fov_px:
            continue
        score = 1.0 / (1.0 + dist)
        if score > best_score:
            best_score = score
            best = (s[0], s[1], e)
    return best


def bezier(dx, dy, t):
    e = t * t * (3.0 - 2.0 * t)
    return dx * e, dy * e


_stop = False
def _sig(*_):
    global _stop; _stop = True


def main(cfg: CombatConfig) -> int:
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    if cfg.hid_backend == "arduino" and cfg.arduino_port:
        hid = ArduinoHid(cfg.arduino_port, cfg.arduino_baud)
    elif cfg.hid_backend == "kmbox" and cfg.kmbox_endpoint:
        hid = KmboxHid(cfg.kmbox_endpoint)
    elif cfg.hid_backend == "adb":
        hid = AdbHid(cfg.adb_serial)
    else:
        hid = NullHid()

    rng = np.random.default_rng()
    last_tick = 0.0
    last_acquire = 0.0
    reaction_due = 0.0
    path = Path(cfg.input_path)
    last_mtime = 0.0

    while not _stop:
        if not path.exists():
            time.sleep(0.02); continue
        try:
            m = path.stat().st_mtime
            if m == last_mtime:
                time.sleep(0.005); continue
            last_mtime = m
            frame = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.01); continue
        w = frame.get("viewport", {}).get("width", 1920)
        h = frame.get("viewport", {}).get("height", 1080)
        cx, cy = w / 2.0, h / 2.0

        target = pick_target(frame, cfg, cx, cy)
        if not target:
            continue
        tx, ty, ent = target

        now = time.monotonic()
        if now - last_acquire > 0.6:
            reaction_due = now + max(cfg.reaction_floor_ms / 1000.0,
                                      rng.lognormal(mean=math.log(0.22), sigma=0.28))
        last_acquire = now
        if now < reaction_due: continue
        if (now - last_tick) * 1000.0 < cfg.tick_ms: continue
        last_tick = now

        dx = (tx - cx) * cfg.sens_scale
        dy = (ty - cy) * cfg.sens_scale

        if abs(dx) < cfg.deadzone_px and abs(dy) < cfg.deadzone_px:
            if cfg.trigger_enabled and abs(tx - cx) < cfg.trigger_fov_px and abs(ty - cy) < cfg.trigger_fov_px:
                hid.click()
            continue

        dx, dy = bezier(dx, dy, 1.0 - cfg.aim_smooth)
        if rng.random() < cfg.miss_rate:
            dx += rng.uniform(-14.0, 14.0)
            dy += rng.uniform(-10.0, 10.0)

        if isinstance(hid, AdbHid):
            hid.tap(tx, ty)
        else:
            hid.move(dx, dy)

    hid.close()
    return 0


def _parse(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-json", required=True)
    ns = ap.parse_args(argv)
    d = json.loads(ns.config_json)
    return CombatConfig(**{k: v for k, v in d.items()
                            if k in CombatConfig.__dataclass_fields__})


if __name__ == "__main__":
    sys.exit(main(_parse(sys.argv[1:])))
