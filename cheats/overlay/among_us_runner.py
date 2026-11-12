#!/usr/bin/env python3
# language: Python 3.11+, file: among_us_runner.py
# Among Us write-loop runner. No-cooldown + fog-off are memory writes
# on a fixed cadence; role-reveal rides on the universal Frame the
# memory_esp pipeline already emits (so you start memory_esp alongside
# to get the overlay).

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "backend"))

from bughunter.game_lab_core.reader_loop import build_walker   # noqa: E402


_stop = False
def _sig(*_): global _stop; _stop = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-json", required=True)
    cfg = json.loads(ap.parse_args().config_json)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    walker = build_walker(
        game=cfg["game"],
        offsets_path=cfg["offsets_path"],
        reader_backend=cfg.get("reader_backend", "dma"),
        process_name=cfg.get("process_name"),
        mobile_endpoint=cfg.get("mobile_endpoint", "127.0.0.1:65510"),
    )
    feature = cfg.get("feature", "all")
    tick = cfg.get("tick_ms", 20) / 1000.0
    do_cooldown = feature in ("no_cooldown", "all")
    do_fog = feature in ("wallhack", "all")

    print(f"[among_us_runner] feature={feature} tick={tick*1000:.0f}ms", flush=True)
    fog_set_once = False
    while not _stop:
        if do_cooldown:
            try:
                walker.write_kill_cooldown_zero()
            except Exception as exc:
                print(f"[among_us_runner] killTimer write error: {exc}", flush=True)
        if do_fog and not fog_set_once:
            # Fog is a one-shot; game doesn't re-set it unless sabotage.
            try:
                if walker.write_fog_off():
                    fog_set_once = True
                    print("[among_us_runner] fog disabled", flush=True)
            except Exception:
                pass
        time.sleep(tick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
