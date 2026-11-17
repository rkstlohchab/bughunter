"""Pure analysis of sampled memory windows: field classification, dissection, diffs.

Inputs are byte snapshots plus the monotonic timestamps at which they were
taken, so every rate and slope here comes from measured time. Values are
little-endian. Classifications are hints for the agent to confirm in game,
never identities.
"""
from __future__ import annotations

import math
import struct

from .engine_map.memview import contains, module_for

_INT_LIMIT = 1_000_000
_FLOAT_MIN, _FLOAT_MAX = 1e-4, 1e7
MAX_ROWS = 128
MAX_CHANGES = 40


def _i32(samples, offset):
    return [struct.unpack_from("<i", s, offset)[0] for s in samples]


def _f32(samples, offset):
    return [struct.unpack_from("<f", s, offset)[0] for s in samples]


def _u64(samples, offset):
    return [struct.unpack_from("<Q", s, offset)[0] for s in samples]


def _float_like(values) -> bool:
    return all(math.isfinite(v) and (v == 0.0 or _FLOAT_MIN <= abs(v) <= _FLOAT_MAX) for v in values)


def _int_like(values) -> bool:
    return all(-_INT_LIMIT <= v <= _INT_LIMIT for v in values)


def _fit(times, values):
    """Least-squares slope (units/s) and R^2; None when degenerate."""
    n = len(values)
    mean_t, mean_v = sum(times) / n, sum(values) / n
    var_t = sum((t - mean_t) ** 2 for t in times)
    var_v = sum((v - mean_v) ** 2 for v in values)
    if var_t <= 0 or var_v <= 0:
        return None
    cov = sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values))
    return cov / var_t, (cov * cov) / (var_t * var_v)


def _changes(times, values):
    out = []
    for index in range(1, len(values)):
        if values[index] != values[index - 1]:
            out.append(index)
    return out


def _behaviour(times, values, *, integer: bool) -> dict:
    changes = _changes(times, values)
    row = {"changes": len(changes), "first": values[0], "last": values[-1],
           "min": min(values), "max": max(values)}
    distinct = set(values)
    if not changes:
        row["label"] = "constant"
        return row
    increasing = all(b >= a for a, b in zip(values, values[1:]))
    decreasing = all(b <= a for a, b in zip(values, values[1:]))
    if distinct <= {0, 1}:
        row["label"] = "flag"
    elif len(distinct) >= 4 and (fit := _fit(times, values)) and fit[1] >= 0.98:
        row["label"] = "timer"
        row["rate_per_s"] = round(fit[0], 6)
        row["r2"] = round(fit[1], 4)
    elif integer and (increasing or decreasing) and len(changes) <= max(1, len(values) // 2):
        steps = sorted({values[i] - values[i - 1] for i in changes})
        row["label"] = "counter"
        row["steps"] = steps[:8]
    else:
        row["label"] = "increasing" if increasing else "decreasing" if decreasing else "changing"
    row["timeline"] = [[round(times[i] - times[0], 4), values[i]] for i in changes[:MAX_CHANGES]]
    return row


def _pointer_target(value, readable, modules):
    hit = module_for(modules, value)
    if hit:
        return f"{hit[0]}+0x{hit[1]:x}"
    return "readable memory" if contains(readable, value, 8) else None


def classify(samples: list[bytes], times: list[float], base: int, *, readable=(), modules=()) -> dict:
    if len(samples) < 2 or len(samples) != len(times):
        raise ValueError("classification needs at least two timed samples")
    size = len(samples[0])
    if any(len(s) != size for s in samples) or size % 4:
        raise ValueError("samples must be equal-length 4-byte multiples")
    readable, modules = list(readable), list(modules)
    rows, consumed = [], set()
    constants = 0
    for offset in range(0, size - 7, 8):
        if (base + offset) % 8:
            continue
        values = _u64(samples, offset)
        if all(v and contains(readable, v, 8) for v in values):
            consumed.update((offset, offset + 4))
            changed = len(set(values)) > 1
            rows.append({"offset": hex(offset), "address": hex(base + offset), "type": "pointer",
                         "label": "pointer_changing" if changed else "pointer",
                         "first": hex(values[0]), "last": hex(values[-1]),
                         "target": _pointer_target(values[-1], readable, modules),
                         "changes": len(_changes(times, values))})
    for offset in range(0, size, 4):
        if offset in consumed:
            continue
        ints = _i32(samples, offset)
        if len(set(ints)) == 1:
            constants += 1
            continue
        floats = _f32(samples, offset)
        if _int_like(ints):
            kind, values = "i32", ints
        elif _float_like(floats):
            kind, values = "f32", [round(v, 6) for v in floats]
        else:
            kind, values = "raw32", [v & 0xFFFFFFFF for v in ints]
        row = {"offset": hex(offset), "address": hex(base + offset), "type": kind,
               **_behaviour(times, values, integer=kind == "i32")}
        rows.append(row)
    by_offset = {int(r["offset"], 16): r for r in rows if r["type"] == "f32"}
    for offset in sorted(by_offset):
        trio = [by_offset.get(offset + d) for d in (0, 4, 8)]
        if all(trio) and not any(r.get("vector") for r in trio):
            sets = [set(int(t * 1e4) for t, _ in r.get("timeline", [])) for r in trio]
            union = set().union(*sets)
            if union and len(set.intersection(*sets)) >= 0.6 * len(union):
                for r in trio:
                    r["vector"] = hex(offset)
                rows.append({"offset": hex(offset), "address": hex(base + offset), "type": "f32x3",
                             "label": "vector3_candidate", "changes": len(union)})
    rows.sort(key=lambda r: (r["type"] == "pointer" and r["label"] == "pointer", int(r["offset"], 16)))
    span = times[-1] - times[0]
    return {"base": hex(base), "size": size, "samples": len(samples),
            "duration_s": round(span, 4), "achieved_hz": round((len(samples) - 1) / span, 2) if span > 0 else None,
            "constant_slots": constants, "rows": rows[:MAX_ROWS], "rows_total": len(rows)}


def dissect(raw: bytes, base: int, *, readable=(), modules=()) -> list[dict]:
    readable, modules = list(readable), list(modules)
    rows = []
    for offset in range(0, len(raw) - len(raw) % 8, 8):
        chunk = raw[offset:offset + 8]
        value = struct.unpack("<Q", chunk)[0]
        lo, hi = struct.unpack("<ii", chunk)
        flo, fhi = struct.unpack("<ff", chunk)
        row = {"offset": hex(offset), "hex": chunk.hex(), "u64": hex(value), "i32": [lo, hi]}
        floats = [round(f, 6) if _float_like([f]) and not _int_like([i]) else None for f, i in ((flo, lo), (fhi, hi))]
        if any(f is not None for f in floats):
            row["f32"] = floats
        target = _pointer_target(value, readable, modules) if value else None
        if target:
            row["pointer"] = target
        text = chunk.rstrip(b"\0")
        if len(text) >= 3 and all(32 <= b < 127 for b in text):
            row["ascii"] = text.decode("ascii")
        rows.append(row)
    return rows


def diff(before: bytes, after: bytes, base: int) -> list[dict]:
    if len(before) != len(after):
        raise ValueError("snapshots differ in size")
    rows = []
    for offset in range(0, len(before) - len(before) % 4, 4):
        a, b = before[offset:offset + 4], after[offset:offset + 4]
        if a == b:
            continue
        ia, ib = struct.unpack("<i", a)[0], struct.unpack("<i", b)[0]
        fa, fb = struct.unpack("<f", a)[0], struct.unpack("<f", b)[0]
        row = {"offset": hex(offset), "address": hex(base + offset), "i32": [ia, ib], "delta_i32": ib - ia}
        if _float_like([fa, fb]) and not _int_like([ia, ib]):
            row["f32"] = [round(fa, 6), round(fb, 6)]
        rows.append(row)
    return rows[:MAX_ROWS * 2]
