#!/usr/bin/env python3
# language: Python 3.11+, file: memory_esp_runner.py
# Universal memory-ESP runner — picks a per-game reader module and drives
# the reader loop. Emits frames via FrameEmitter (shared file + UDP).
#
# This is the entry point the memory_esp adapter spawns. Config comes from
# --config-json; falls back to CLI flags.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the backend package importable when run outside the installed env
HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "backend"))

from bughunter.game_lab_core import FrameEmitter                   # noqa: E402
from bughunter.game_lab_core.reader_loop import ReaderLoop, build_walker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-json", required=True)
    args = ap.parse_args()
    cfg = json.loads(args.config_json)

    walker = build_walker(
        game=cfg["game"],
        offsets_path=cfg["offsets_path"],
        reader_backend=cfg.get("reader_backend", "dma"),
        process_name=cfg.get("process_name"),
        mobile_endpoint=cfg.get("mobile_endpoint", "127.0.0.1:65510"),
    )
    emitter = FrameEmitter(
        shared_file=cfg.get("shared_file", "/tmp/gamelab_frame.json"),
        udp=(cfg.get("udp_host", "127.0.0.1"),
             int(cfg.get("udp_port", 65501))) if cfg.get("udp_enabled", True) else None,
    )
    loop = ReaderLoop(
        walker=walker, emitter=emitter,
        target_hz=int(cfg.get("target_hz", 200)),
        screen_w=int(cfg.get("screen_w", 1920)),
        screen_h=int(cfg.get("screen_h", 1080)),
    )
    print(f"[memory_esp] {cfg['game']} reader online -> "
          f"{cfg.get('shared_file')}", flush=True)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
