#!/usr/bin/env python3
# language: Python 3.11+, file: radar_window.py
# 2D top-down radar window. Reads the universal Frame schema from
# /tmp/gamelab_frame.json or the UDP socket, plots enemies on a plane.
#
# Standalone: python3 radar_window.py --input shared_file --range 500

from __future__ import annotations

import argparse
import json
import math
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class RadarConfig:
    input: str = "shared_file"
    shared_file: str = "/tmp/gamelab_frame.json"
    udp_port: int = 65502
    size_px: int = 420
    range_m: float = 300.0
    local_team_filter: bool = True
    rotate_with_camera: bool = True
    show_loot: bool = False


_stop = False


def _handle_sig(*_):
    global _stop
    _stop = True


def _poll_frame(cfg: RadarConfig, sock: Optional[socket.socket]) -> Optional[dict]:
    if sock:
        try:
            data, _ = sock.recvfrom(65535)
            return json.loads(data.decode("utf-8", errors="replace"))
        except socket.timeout:
            return None
        except Exception:
            return None
    path = Path(cfg.shared_file)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main(cfg: RadarConfig) -> int:
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)
    sock: Optional[socket.socket] = None
    if cfg.input == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", cfg.udp_port))
        sock.settimeout(0.05)
    win = "GameLab radar"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, cfg.size_px, cfg.size_px)
    canvas = np.zeros((cfg.size_px, cfg.size_px, 3), dtype=np.uint8)
    center = cfg.size_px // 2
    scale = (cfg.size_px * 0.47) / cfg.range_m

    while not _stop:
        frame = _poll_frame(cfg, sock)
        canvas[:] = 20
        cv2.circle(canvas, (center, center), int(cfg.size_px * 0.47),
                   (80, 80, 80), 1)
        cv2.circle(canvas, (center, center), int(cfg.size_px * 0.31),
                   (60, 60, 60), 1)
        cv2.circle(canvas, (center, center), int(cfg.size_px * 0.16),
                   (60, 60, 60), 1)
        cv2.line(canvas, (center, 0), (center, cfg.size_px), (60, 60, 60), 1)
        cv2.line(canvas, (0, center), (cfg.size_px, center), (60, 60, 60), 1)
        cv2.circle(canvas, (center, center), 4, (0, 255, 0), -1)

        if frame:
            local_pos = frame.get("local_pos", (0, 0, 0))
            local_team = frame.get("local_team", 0)
            cam_yaw = frame.get("camera_yaw", 0.0)
            cos_y = math.cos(math.radians(-cam_yaw)) if cfg.rotate_with_camera else 1.0
            sin_y = math.sin(math.radians(-cam_yaw)) if cfg.rotate_with_camera else 0.0

            for ent in frame.get("entities", []):
                if cfg.local_team_filter and ent.get("team") == local_team:
                    continue
                pos = ent.get("center_world", (0, 0, 0))
                dx = pos[0] - local_pos[0]
                dy = pos[1] - local_pos[1]
                rx = dx * cos_y - dy * sin_y
                ry = dx * sin_y + dy * cos_y
                px = int(center + rx * scale)
                py = int(center - ry * scale)
                if not (0 <= px < cfg.size_px and 0 <= py < cfg.size_px):
                    continue
                color = (0, 0, 255)
                if ent.get("hp", 0) < 40:
                    color = (0, 165, 255)
                cv2.circle(canvas, (px, py), 5, color, -1)
                dist = ent.get("distance_m", 0.0)
                cv2.putText(canvas, f"{dist:.0f}", (px + 6, py + 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)

            if cfg.show_loot:
                for item in frame.get("loot", []):
                    pos = item.get("center_world", (0, 0, 0))
                    dx = pos[0] - local_pos[0]
                    dy = pos[1] - local_pos[1]
                    rx = dx * cos_y - dy * sin_y
                    ry = dx * sin_y + dy * cos_y
                    px = int(center + rx * scale)
                    py = int(center - ry * scale)
                    if not (0 <= px < cfg.size_px and 0 <= py < cfg.size_px):
                        continue
                    cv2.circle(canvas, (px, py), 2, (100, 255, 100), -1)

        cv2.imshow(win, canvas)
        cv2.waitKey(1)
        time.sleep(0.01)
    if sock:
        sock.close()
    cv2.destroyAllWindows()
    return 0


def _parse(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="shared_file",
                    choices=["shared_file", "udp"])
    ap.add_argument("--shared-file", default="/tmp/gamelab_frame.json")
    ap.add_argument("--udp-port", type=int, default=65502)
    ap.add_argument("--size", type=int, default=420)
    ap.add_argument("--range", type=float, default=300.0)
    ap.add_argument("--no-rotate", action="store_true")
    ap.add_argument("--loot", action="store_true")
    ap.add_argument("--config-json")
    ns = ap.parse_args(argv)
    if ns.config_json:
        d = json.loads(ns.config_json)
        return RadarConfig(**{k: v for k, v in d.items()
                               if k in RadarConfig.__dataclass_fields__})
    return RadarConfig(input=ns.input, shared_file=ns.shared_file,
                       udp_port=ns.udp_port, size_px=ns.size,
                       range_m=getattr(ns, "range"),
                       rotate_with_camera=not ns.no_rotate,
                       show_loot=ns.loot)


if __name__ == "__main__":
    sys.exit(main(_parse(sys.argv[1:])))
