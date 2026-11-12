#!/usr/bin/env python3
# language: Python 3.11+, file: pcileech_reader.py, target: reader PC (Linux/Win)
# PCILeech + MemProcFS reader loop for DMA cheats.
# Walks per-game offsets to pull entity list, projects world-to-screen,
# optionally pushes aim deltas to KMBox Net.
#
# Config via --config-json '<json>'. Standalone with explicit flags.
#
# Requires: pip install memprocfs numpy + PCILeech firmware + FPGA card.

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


_stop = False


def _handle_sig(_s, _f):
    global _stop
    _stop = True


@dataclass
class Config:
    process_name: str = "cs2.exe"
    offsets_path: str = ""
    kmbox_endpoint: Optional[str] = None     # host:port
    humanize: bool = True
    project_id: Optional[str] = None
    game: Optional[str] = None
    esp_overlay: bool = True
    aimbot_enabled: bool = False
    screen_w: int = 1920
    screen_h: int = 1080
    sens_scale: float = 1.0
    aim_tick_ms: int = 90
    miss_rate: float = 0.08
    reaction_floor_ms: int = 180


class KmboxNet:
    def __init__(self, endpoint: str):
        import socket
        host, port = endpoint.split(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(1.5)
        self.sock.connect((host, int(port)))
    def move(self, dx, dy):
        self.sock.sendall((json.dumps({"cmd": "mouse_move",
                                        "dx": int(dx), "dy": int(dy)})
                            + "\n").encode())
    def close(self):
        try: self.sock.close()
        except Exception: pass


def world_to_screen(pos, mat, w, h):
    """Row-major 4x4 view-projection. Returns (x, y) in screen space or None if culled."""
    x, y, z = pos
    cx = mat[0][0]*x + mat[0][1]*y + mat[0][2]*z + mat[0][3]
    cy = mat[1][0]*x + mat[1][1]*y + mat[1][2]*z + mat[1][3]
    cw = mat[3][0]*x + mat[3][1]*y + mat[3][2]*z + mat[3][3]
    if cw <= 0.1:
        return None
    ndc_x = cx / cw
    ndc_y = cy / cw
    sx = (1.0 + ndc_x) * 0.5 * w
    sy = (1.0 - ndc_y) * 0.5 * h
    return sx, sy


def main(cfg: Config) -> int:
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        import memprocfs
    except Exception as exc:
        print(f"[pcileech_reader] memprocfs missing: {exc}", file=sys.stderr)
        print("    pip install memprocfs   # requires PCILeech firmware + FPGA", file=sys.stderr)
        return 2

    if not cfg.offsets_path or not Path(cfg.offsets_path).exists():
        print(f"[pcileech_reader] offsets not found: {cfg.offsets_path}", file=sys.stderr)
        return 3
    offsets_blob = json.loads(Path(cfg.offsets_path).read_text())
    offsets = offsets_blob.get("offsets", {})
    if not offsets:
        print("[pcileech_reader] empty offsets", file=sys.stderr)
        return 4

    print(f"[pcileech_reader] opening DMA...", flush=True)
    vmm = memprocfs.Vmm(["-device", "fpga://"])
    proc = None
    for p in vmm.process_list():
        if p.name.lower() == cfg.process_name.lower():
            proc = p
            break
    if proc is None:
        print(f"[pcileech_reader] process not found: {cfg.process_name}", file=sys.stderr)
        return 5
    print(f"[pcileech_reader] attached pid={proc.pid} name={proc.name}", flush=True)

    # client.dll (CS2 example) — adjust per game
    base_module_name = offsets.get("base_module", proc.name)
    base = None
    for m in proc.module_list():
        if m.name.lower() == base_module_name.lower():
            base = m.base
            break
    if base is None:
        print(f"[pcileech_reader] module {base_module_name} not found", file=sys.stderr)
        return 6

    kmbox = KmboxNet(cfg.kmbox_endpoint) if (cfg.aimbot_enabled and cfg.kmbox_endpoint) else None
    rng = np.random.default_rng()
    last_tick = 0.0
    last_acquire = 0.0
    reaction_due = 0.0

    # Pre-resolve static pointers if named in offsets
    dw_entity_list   = offsets.get("dwEntityList", 0)
    dw_local_player  = offsets.get("dwLocalPlayerController", 0)
    dw_view_matrix   = offsets.get("dwViewMatrix", 0)
    team_off         = offsets.get("m_iTeamNum", 0)
    health_off       = offsets.get("m_iHealth", 0)
    scene_off        = offsets.get("m_pGameSceneNode", 0)
    bones_off_struct = offsets.get("m_modelState_m_bonesArray", 0)
    pawn_off         = offsets.get("m_pPawn", 0)   # local player pawn offset

    def rd_u64(a): return int.from_bytes(vmm.memory.read(proc.pid, a, 8), "little", signed=False)
    def rd_u32(a): return int.from_bytes(vmm.memory.read(proc.pid, a, 4), "little", signed=False)
    def rd_i32(a): return int.from_bytes(vmm.memory.read(proc.pid, a, 4), "little", signed=True)
    def rd_f32(a): return float(np.frombuffer(vmm.memory.read(proc.pid, a, 4), dtype=np.float32)[0])
    def rd_vec3(a):
        b = vmm.memory.read(proc.pid, a, 12)
        return tuple(float(v) for v in np.frombuffer(b, dtype=np.float32))
    def rd_matrix(a):
        b = vmm.memory.read(proc.pid, a, 64)
        return np.frombuffer(b, dtype=np.float32).reshape(4, 4).tolist()

    while not _stop:
        try:
            view_matrix = rd_matrix(base + dw_view_matrix)
            local_controller = rd_u64(base + dw_local_player)
            local_pawn = rd_u64(local_controller + pawn_off) if local_controller else 0
            local_team = rd_i32(local_pawn + team_off) if local_pawn else 0
            entity_list = base + dw_entity_list
            targets = []
            for i in range(1, 64):
                list_entry = rd_u64(entity_list + 8 * (i >> 9) + 16)
                if not list_entry:
                    continue
                pawn = rd_u64(list_entry + 120 * (i & 0x1FF))
                if not pawn:
                    continue
                team = rd_i32(pawn + team_off)
                hp = rd_i32(pawn + health_off)
                if team == local_team or hp <= 0:
                    continue
                scene = rd_u64(pawn + scene_off)
                if not scene:
                    continue
                bone_array = rd_u64(scene + bones_off_struct)
                if not bone_array:
                    continue
                head = rd_vec3(bone_array + 6 * 32)   # bone 6 head, 32-byte stride
                projected = world_to_screen(head, view_matrix,
                                             cfg.screen_w, cfg.screen_h)
                if projected is None:
                    continue
                targets.append({"id": i, "pos2d": projected,
                                "hp": hp, "team": team})

            # Push to overlay (shared file, read by esp_overlay.py)
            if cfg.esp_overlay:
                out = {"ts": time.time(), "view": view_matrix,
                       "targets": targets}
                Path("/tmp/gamelab_dma_frame.json").write_text(
                    json.dumps(out), encoding="utf-8")

            if cfg.aimbot_enabled and kmbox and targets:
                cx = cfg.screen_w / 2.0
                cy = cfg.screen_h / 2.0
                best = min(targets,
                            key=lambda t: (t["pos2d"][0] - cx) ** 2 +
                                          (t["pos2d"][1] - cy) ** 2)
                now = time.monotonic()
                if now - last_acquire > 0.6:
                    reaction_due = now + max(cfg.reaction_floor_ms / 1000.0,
                                             rng.lognormal(mean=np.log(0.22),
                                                           sigma=0.28))
                last_acquire = now
                if now < reaction_due:
                    continue
                if (now - last_tick) * 1000.0 < cfg.aim_tick_ms:
                    continue
                last_tick = now
                dx = (best["pos2d"][0] - cx) * cfg.sens_scale
                dy = (best["pos2d"][1] - cy) * cfg.sens_scale
                if cfg.humanize and rng.random() < cfg.miss_rate:
                    dx += rng.uniform(-14.0, 14.0)
                    dy += rng.uniform(-10.0, 10.0)
                kmbox.move(int(round(dx)), int(round(dy)))

            time.sleep(0.005)   # ~200Hz read loop
        except Exception as exc:
            print(f"[pcileech_reader] read error: {exc}", flush=True)
            time.sleep(0.1)

    if kmbox: kmbox.close()
    return 0


def _parse_argv(argv: list[str]) -> Config:
    ap = argparse.ArgumentParser(description="PCILeech + MemProcFS reader")
    ap.add_argument("--config-json", type=str)
    ap.add_argument("--process", default="cs2.exe")
    ap.add_argument("--offsets", default="")
    ap.add_argument("--kmbox", default=None, help="KMBox endpoint host:port")
    ap.add_argument("--aimbot", action="store_true")
    ap.add_argument("--no-overlay", action="store_true")
    ns = ap.parse_args(argv)
    if ns.config_json:
        data = json.loads(ns.config_json)
        return Config(**{k: v for k, v in data.items()
                         if k in Config.__dataclass_fields__})
    cfg = Config()
    cfg.process_name = ns.process
    cfg.offsets_path = ns.offsets
    cfg.kmbox_endpoint = ns.kmbox
    cfg.aimbot_enabled = ns.aimbot
    cfg.esp_overlay = not ns.no_overlay
    return cfg


if __name__ == "__main__":
    sys.exit(main(_parse_argv(sys.argv[1:])))
