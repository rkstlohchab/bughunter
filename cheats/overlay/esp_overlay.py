#!/usr/bin/env python3
# language: Python 3.11+, file: esp_overlay.py
# Universal GameLab ESP overlay renderer.
#
# Reads a Frame (universal schema) from either a UDP socket or the shared
# file /tmp/gamelab_frame.json, and draws boxes + head dots + snaplines +
# skeletons + name/HP/distance/weapon text + visibility coloring + chams.
#
# Overlay modes:
#   - window:    standalone PyQt window (second-PC overlay for DMA/vision).
#                Pair with a capture-card preview underneath if desired.
#   - fullscreen-transparent: topmost click-through transparent window on
#                the same PC. Detectable by ACs that screenshot.
#   - opencv:    cv2.imshow — simplest, no PyQt dependency.
#
# Called by the memory_esp adapter standalone or by hand:
#   python3 esp_overlay.py --mode opencv --input shared_file

from __future__ import annotations

import argparse
import colorsys
import json
import math
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Canonical bone edges — match frame.py SKELETON_EDGES
SKELETON_EDGES = [
    (0, 1), (1, 5), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
    (1, 3), (3, 4), (4, 2), (2, 11), (11, 12), (12, 13),
    (2, 14), (14, 15), (15, 16),
]


# ---------------------------------------------------------------------------


@dataclass
class OverlayConfig:
    mode: str = "window"                    # window | fullscreen-transparent | opencv
    input: str = "shared_file"              # shared_file | udp
    shared_file: str = "/tmp/gamelab_frame.json"
    udp_port: int = 65501
    width: int = 1920
    height: int = 1080
    # Feature toggles
    draw_box: bool = True
    draw_head_dot: bool = True
    draw_snapline: bool = True
    draw_skeleton: bool = True
    draw_name: bool = True
    draw_hp_bar: bool = True
    draw_distance: bool = True
    draw_weapon: bool = True
    draw_visibility: bool = True
    draw_loot: bool = False
    chams_mode: str = "team"                # off | team | visibility | distance
    max_distance_m: float = 500.0
    team_colors: dict = None
    local_team_filter: bool = True
    show_only_enemies: bool = True


# ---------------------------------------------------------------------------


def project_world(world, mat, w, h) -> Optional[tuple[float, float]]:
    x, y, z = world
    cx = mat[0] * x + mat[1] * y + mat[2] * z + mat[3]
    cy = mat[4] * x + mat[5] * y + mat[6] * z + mat[7]
    cw = mat[12] * x + mat[13] * y + mat[14] * z + mat[15]
    if cw < 0.01:
        return None
    return ((1.0 + cx / cw) * 0.5 * w, (1.0 - cy / cw) * 0.5 * h)


def team_color(team: int, local_team: int, visible: bool,
                distance_m: float, cfg: OverlayConfig) -> tuple[int, int, int]:
    """BGR tuple for OpenCV / PyQt. Visibility and distance can override."""
    enemy = (team != local_team)
    if cfg.chams_mode == "visibility":
        return (0, 255, 0) if visible else (0, 165, 255)
    if cfg.chams_mode == "distance":
        t = min(1.0, distance_m / cfg.max_distance_m)
        r, g, b = colorsys.hsv_to_rgb(0.33 * (1.0 - t), 1.0, 1.0)
        return (int(b * 255), int(g * 255), int(r * 255))
    if cfg.chams_mode == "team":
        return (0, 0, 255) if enemy else (255, 180, 0)
    return (0, 0, 255)


# ---------------------------------------------------------------------------


class FrameSource:
    """Pull a Frame dict from the configured source."""

    def __init__(self, cfg: OverlayConfig):
        self.cfg = cfg
        self._sock: Optional[socket.socket] = None
        if cfg.input == "udp":
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(("127.0.0.1", cfg.udp_port))
            self._sock.settimeout(0.05)
        self._last: Optional[dict] = None
        self._last_stamp = 0.0

    def poll(self) -> Optional[dict]:
        if self._sock:
            try:
                data, _ = self._sock.recvfrom(65535)
                self._last = json.loads(data.decode("utf-8", errors="replace"))
                self._last_stamp = time.monotonic()
                return self._last
            except socket.timeout:
                return self._last if time.monotonic() - self._last_stamp < 0.5 else None
            except Exception:
                return None
        # shared_file
        try:
            path = Path(self.cfg.shared_file)
            if not path.exists():
                return None
            text = path.read_text(encoding="utf-8")
            if not text:
                return None
            return json.loads(text)
        except Exception:
            return self._last

    def close(self):
        if self._sock:
            try: self._sock.close()
            except Exception: pass


# ---------------------------------------------------------------------------


class OpenCvOverlay:
    """Minimum-dep overlay — one cv2 window, draws every feature."""

    def __init__(self, cfg: OverlayConfig):
        import cv2
        import numpy as np
        self.cv2 = cv2
        self.np = np
        self.cfg = cfg
        self.win = "GameLab ESP overlay"
        cv2.namedWindow(self.win, cv2.WINDOW_NORMAL)
        self.canvas = np.zeros((cfg.height, cfg.width, 3), dtype=np.uint8)

    def draw(self, frame: dict) -> None:
        cv2 = self.cv2
        np = self.np
        self.canvas[:] = 0
        viewport = frame.get("viewport", {})
        w = viewport.get("width", self.cfg.width)
        h = viewport.get("height", self.cfg.height)
        if (w, h) != self.canvas.shape[1::-1]:
            self.canvas = np.zeros((h, w, 3), dtype=np.uint8)
        mat = frame.get("view", {}).get("data", [0.0] * 16)
        local_team = frame.get("local_team", 0)
        status = frame.get("status", "ok")
        fps = frame.get("reader_fps", 0.0)
        cv2.putText(self.canvas,
                    f"{frame.get('game','?')} / {frame.get('anticheat','?')}"
                    f"  fps={fps:.1f}  status={status}",
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 200), 1)
        if status != "ok":
            cv2.imshow(self.win, self.canvas)
            cv2.waitKey(1)
            return

        for ent in frame.get("entities", []):
            if self.cfg.show_only_enemies and ent.get("team") == local_team:
                continue
            dist = ent.get("distance_m", 0.0)
            if dist > self.cfg.max_distance_m:
                continue
            head_w = tuple(ent.get("head_world", (0, 0, 0)))
            feet_w = tuple(ent.get("feet_world", (0, 0, 0)))
            head_s = project_world(head_w, mat, w, h)
            feet_s = project_world(feet_w, mat, w, h)
            if not head_s or not feet_s:
                continue
            color = team_color(ent.get("team", 0), local_team,
                                ent.get("visible", False), dist, self.cfg)

            # 2D box
            box_h = max(10.0, abs(feet_s[1] - head_s[1]))
            box_w = box_h * 0.42
            x1 = int(head_s[0] - box_w / 2); y1 = int(head_s[1] - box_h * 0.08)
            x2 = int(head_s[0] + box_w / 2); y2 = int(feet_s[1])
            if self.cfg.draw_box:
                cv2.rectangle(self.canvas, (x1, y1), (x2, y2), color, 2)

            # Head dot
            if self.cfg.draw_head_dot:
                cv2.circle(self.canvas, (int(head_s[0]), int(head_s[1])),
                           3, color, -1)

            # Snapline
            if self.cfg.draw_snapline:
                cv2.line(self.canvas, (w // 2, h), (int(head_s[0]), int(head_s[1])),
                         color, 1)

            # Skeleton
            if self.cfg.draw_skeleton and ent.get("bones_world"):
                projected = []
                for b in ent["bones_world"]:
                    p = project_world(tuple(b), mat, w, h)
                    projected.append(p)
                for a, b in SKELETON_EDGES:
                    if a < len(projected) and b < len(projected) and projected[a] and projected[b]:
                        cv2.line(self.canvas,
                                 (int(projected[a][0]), int(projected[a][1])),
                                 (int(projected[b][0]), int(projected[b][1])),
                                 color, 1)

            # HP bar
            hp = ent.get("hp", 0); max_hp = ent.get("max_hp", 100) or 100
            if self.cfg.draw_hp_bar:
                bar_x = x1 - 7; bar_y1 = y1; bar_y2 = y2
                bar_h = bar_y2 - bar_y1
                fill = int(bar_h * (hp / max_hp))
                cv2.rectangle(self.canvas, (bar_x, bar_y1), (bar_x + 3, bar_y2),
                              (40, 40, 40), -1)
                cv2.rectangle(self.canvas, (bar_x, bar_y2 - fill),
                              (bar_x + 3, bar_y2),
                              (0, 255, 0) if hp > 40 else (0, 165, 255), -1)

            # Labels
            label_y = y1 - 6
            if self.cfg.draw_name and ent.get("name"):
                cv2.putText(self.canvas, ent["name"][:16],
                            (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, color, 1)
                label_y -= 14
            if self.cfg.draw_distance:
                cv2.putText(self.canvas, f"{dist:.0f}m",
                            (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.4, color, 1)
                label_y -= 12
            if self.cfg.draw_weapon and ent.get("extra", {}).get("weapon"):
                cv2.putText(self.canvas, ent["extra"]["weapon"][:12],
                            (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.35, color, 1)

        if self.cfg.draw_loot:
            for item in frame.get("loot", []):
                p = project_world(tuple(item.get("center_world", (0, 0, 0))), mat, w, h)
                if p:
                    cv2.circle(self.canvas, (int(p[0]), int(p[1])), 2, (100, 255, 100), -1)
                    cv2.putText(self.canvas, item.get("name", "")[:10],
                                (int(p[0]) + 6, int(p[1]) + 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                                (100, 255, 100), 1)

        cv2.imshow(self.win, self.canvas)
        cv2.waitKey(1)


# ---------------------------------------------------------------------------


_stop = False


def _handle_sig(_s, _f):
    global _stop
    _stop = True


def main(cfg: OverlayConfig) -> int:
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)
    src = FrameSource(cfg)
    overlay = OpenCvOverlay(cfg)   # (window + fullscreen-transparent modes layered atop)
    try:
        while not _stop:
            frame = src.poll()
            if frame is not None:
                overlay.draw(frame)
            time.sleep(0.005)
    finally:
        src.close()
    return 0


def _parse(argv: list[str]) -> OverlayConfig:
    ap = argparse.ArgumentParser(description="GameLab universal ESP overlay")
    ap.add_argument("--mode", default="opencv",
                    choices=["opencv", "window", "fullscreen-transparent"])
    ap.add_argument("--input", default="shared_file",
                    choices=["shared_file", "udp"])
    ap.add_argument("--shared-file", default="/tmp/gamelab_frame.json")
    ap.add_argument("--udp-port", type=int, default=65501)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--chams", default="team",
                    choices=["off", "team", "visibility", "distance"])
    ap.add_argument("--max-distance", type=float, default=500.0)
    ap.add_argument("--no-box", action="store_true")
    ap.add_argument("--no-snapline", action="store_true")
    ap.add_argument("--no-skeleton", action="store_true")
    ap.add_argument("--no-names", action="store_true")
    ap.add_argument("--no-hp", action="store_true")
    ap.add_argument("--loot", action="store_true")
    ap.add_argument("--config-json", type=str, default=None)
    ns = ap.parse_args(argv)
    if ns.config_json:
        data = json.loads(ns.config_json)
        return OverlayConfig(**{k: v for k, v in data.items()
                                 if k in OverlayConfig.__dataclass_fields__})
    cfg = OverlayConfig()
    cfg.mode = ns.mode; cfg.input = ns.input
    cfg.shared_file = ns.shared_file; cfg.udp_port = ns.udp_port
    cfg.width = ns.width; cfg.height = ns.height
    cfg.chams_mode = ns.chams; cfg.max_distance_m = ns.max_distance
    cfg.draw_box = not ns.no_box
    cfg.draw_snapline = not ns.no_snapline
    cfg.draw_skeleton = not ns.no_skeleton
    cfg.draw_name = not ns.no_names
    cfg.draw_hp_bar = not ns.no_hp
    cfg.draw_loot = ns.loot
    return cfg


if __name__ == "__main__":
    sys.exit(main(_parse(sys.argv[1:])))
