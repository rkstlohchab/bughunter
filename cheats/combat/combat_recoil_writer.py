#!/usr/bin/env python3
# language: Python 3.11+, file: combat_recoil_writer.py
# Memory-write recoil suppressor / spread zeroing.
#
# ONLY runs with an explicit write-capable MemoryReader backend (DMA with
# PCILeech supports writes via vmm.memory.write; external RPM supports
# WriteProcessMemory with PROCESS_VM_WRITE; the Android bridge exposes a
# "write" verb when the Zygisk module is built with WRITE_ENABLED=1).
#
# Writes a zero to the per-game recoil struct at the configured address
# every tick. If the game nulls it back, the loop sustains the write. If
# the game uses server-side authoritative recoil (CS2, Valorant), writes
# do nothing — this path is for titles with client-side recoil state
# (older engine titles, some mobile builds).

from __future__ import annotations

import argparse
import json
import signal
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "backend"))


@dataclass
class RecoilConfig:
    game: str = ""
    offsets_path: str = ""
    reader_backend: str = "dma"
    process_name: str = ""
    targets: dict | None = None             # name -> address (resolved)
    tick_ms: int = 20                        # 50 Hz write cadence


_stop = False
def _sig(*_):
    global _stop; _stop = True


def main(cfg: RecoilConfig) -> int:
    signal.signal(signal.SIGINT, _sig); signal.signal(signal.SIGTERM, _sig)
    if cfg.reader_backend == "dma":
        import memprocfs
        vmm = memprocfs.Vmm(["-device", "fpga://"])
        proc = None
        for p in vmm.process_list():
            if p.name.lower() == cfg.process_name.lower():
                proc = p; break
        if not proc:
            print(f"[recoil] process missing: {cfg.process_name}", file=sys.stderr)
            return 2
        def write_f32(addr, value):
            vmm.memory.write(proc.pid, addr, struct.pack("<f", value))
        def write_i32(addr, value):
            vmm.memory.write(proc.pid, addr, struct.pack("<i", value))
    elif cfg.reader_backend == "rpm":
        from bughunter.game_lab_core.memprocfs_backend import WindowsRpmReader
        # Writes need a PROCESS_VM_WRITE handle — extend the reader first.
        print("[recoil] RPM write not wired — extend WindowsRpmReader with WritePM.", file=sys.stderr)
        return 3
    else:
        print(f"[recoil] unknown backend {cfg.reader_backend}", file=sys.stderr)
        return 4

    offsets = json.loads(Path(cfg.offsets_path).read_text()) if Path(cfg.offsets_path).exists() else {}
    offsets = offsets.get("offsets", offsets)
    targets = cfg.targets or offsets.get("recoil_targets", {})
    if not targets:
        print("[recoil] no recoil_targets in offsets; nothing to do", file=sys.stderr)
        return 0

    while not _stop:
        for name, addr in targets.items():
            try:
                write_f32(int(addr), 0.0)
            except Exception:
                pass
        time.sleep(cfg.tick_ms / 1000.0)
    return 0


def _parse(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-json", required=True)
    ns = ap.parse_args(argv)
    d = json.loads(ns.config_json)
    return RecoilConfig(**{k: v for k, v in d.items()
                            if k in RecoilConfig.__dataclass_fields__})


if __name__ == "__main__":
    sys.exit(main(_parse(sys.argv[1:])))
