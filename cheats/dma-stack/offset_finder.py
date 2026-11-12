#!/usr/bin/env python3
# language: Python 3.11+, file: offset_finder.py
# AOB signature scanner against a running process (via PCILeech/MemProcFS) OR
# a dumped game image on disk. Reads a signature set from JSON, emits new
# offsets.
#
# Usage:
#   # scan a live process via DMA:
#   python3 offset_finder.py --process cs2.exe --signatures signatures/cs2.sig.json
#   # scan a dumped exe on disk:
#   python3 offset_finder.py --image /tmp/val.exe --signatures signatures/valorant.sig.json

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path


def pattern_to_regex(pat: str) -> re.Pattern:
    """IDA-style pattern 48 8B ? ? C3 -> compiled bytes regex."""
    parts = pat.strip().split()
    out = []
    for p in parts:
        if p in ("?", "??"):
            out.append(b".")
        else:
            out.append(re.escape(bytes.fromhex(p)))
    return re.compile(b"".join(out), re.DOTALL)


def resolve_rip_rel(data: bytes, hit: int, rip_offset: int, operand_size: int = 4) -> int:
    """For `lea rax, [rip+disp32]`, resolve the absolute RVA.
    hit = start of instruction; rip_offset = byte offset to the disp32 field;
    instr_len = rip_offset + operand_size (RIP points past the instruction)."""
    disp_bytes = data[hit + rip_offset : hit + rip_offset + operand_size]
    disp = struct.unpack("<i", disp_bytes)[0]
    rip = hit + rip_offset + operand_size
    return rip + disp


def scan_image(image: bytes, sigs: dict) -> dict:
    results: dict = {}
    for name, entry in sigs.items():
        pat = entry.get("pattern", "")
        if not pat:
            results[name] = None; continue
        regex = pattern_to_regex(pat)
        m = regex.search(image)
        if not m:
            results[name] = None
            continue
        hit = m.start()
        resolved = hit
        # if entry declares RIP-relative resolution:
        if entry.get("rip_relative"):
            rip_off = int(entry.get("rip_offset", 0))
            op_size = int(entry.get("operand_size", 4))
            resolved = resolve_rip_rel(image, hit, rip_off, op_size)
        rel = entry.get("rel_offset", 0)
        resolved += int(rel)
        results[name] = resolved
    return results


def scan_process(process_name: str, sigs: dict, module_hint: str = "") -> dict:
    try:
        import memprocfs
    except Exception as exc:
        print(f"memprocfs missing: {exc}", file=sys.stderr)
        return {}
    vmm = memprocfs.Vmm(["-device", "fpga://"])
    proc = None
    for p in vmm.process_list():
        if p.name.lower() == process_name.lower():
            proc = p
            break
    if proc is None:
        print(f"process not found: {process_name}", file=sys.stderr)
        return {}
    module = None
    hint = module_hint.lower() if module_hint else proc.name.lower()
    for m in proc.module_list():
        if m.name.lower() == hint:
            module = m
            break
    if module is None:
        print(f"module not found: {hint}", file=sys.stderr)
        return {}
    size = module.size or (16 * 1024 * 1024)
    data = vmm.memory.read(proc.pid, module.base, size)
    rvas = scan_image(data, sigs)
    out = {}
    for name, rva in rvas.items():
        if rva is None:
            out[name] = None
        else:
            out[name] = rva   # relative to module base
    out["base_module"] = module.name
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="AOB offset scanner")
    ap.add_argument("--signatures", required=True, help="signatures JSON")
    ap.add_argument("--image", help="scan a dumped image on disk")
    ap.add_argument("--process", help="scan a live process via DMA")
    ap.add_argument("--module", default="", help="module name hint (for live scan)")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ns = ap.parse_args(argv)

    sigs_blob = json.loads(Path(ns.signatures).read_text())
    sigs = sigs_blob.get("signatures", sigs_blob)

    if ns.image:
        data = Path(ns.image).read_bytes()
        results = scan_image(data, sigs)
    elif ns.process:
        results = scan_process(ns.process, sigs, module_hint=ns.module)
    else:
        print("need --image or --process", file=sys.stderr)
        return 2

    out = {"offsets": {k: v for k, v in results.items() if v is not None},
           "missed": [k for k, v in results.items() if v is None]}
    if ns.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"[offset_finder] hits: {len(out['offsets'])} / {len(results)}")
        for k, v in out['offsets'].items():
            print(f"  {k} = 0x{v:X}" if isinstance(v, int) else f"  {k} = {v}")
        if out['missed']:
            print(f"  missed: {', '.join(out['missed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
