"""P06 — Among Us-specific feature: local kill-cooldown.

Field layout verified against the Cpp2IL output produced by P02's
static replay (see `claude-P02-static-replay-001/static-analysis/
derived/DiffableCs/Assembly-CSharp/PlayerControl.cs`):

  * ``PlayerControl.LocalPlayer`` — static field offset 0x0.
  * ``PlayerControl.killTimer`` — instance field offset 0xB0 (float32).
  * ``PlayerControl.SetKillTimer(float)`` — the method that writes the
    field under normal gameplay.

Build fingerprint for the inspected target: Among Us 2026.11.3 arm64,
Unity 2022.3.62f3, IL2CPP metadata version 31.

Resolver strategy:

  1. Find a ``PlayerControl*`` in writable memory that passes a basic
     shape check (non-null, pointer-aligned, within writable regions).
  2. Observe countdown behavior without writing. This narrows candidates;
     independent LocalPlayer/class/root evidence is still required.
  3. Mark the recipe ``candidate`` and ask the operator to observe
     via :mod:`~.features.FeatureRun`. **The resolver NEVER elevates
     status on its own**; see :mod:`~.mappings.MappingStore`.

Cleanup contract: the plan requires that we NOT restore a stale
captured countdown to a running timer. :func:`build_kill_cooldown_recipe`
returns ``disable_value=None`` so the feature stops without a late
write; normal behavior returns on the next round transition or when
``SetKillTimer`` is called by the game itself.
"""
from __future__ import annotations

import struct
import time
import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .mappings import MappingStore, Recipe


SUPPORTED_BUILD_FINGERPRINT = "3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992"
LOCAL_PLAYER_STATIC_OFFSET = 0x0
KILL_TIMER_INSTANCE_OFFSET = 0xB0
KILL_TIMER_VALUE_TYPE = "f32"

DEFAULT_KILL_COOLDOWN_SETTINGS = (10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 45.0, 60.0)


@dataclass
class LocalPlayerCandidate:
    address: int
    kill_timer: float
    reason: str


def build_kill_cooldown_recipe(
    store,
    *,
    target_fingerprint: str,
    player_control_class_static_fields_rva: Optional[int] = None,
    module_name: str = "UnityFramework",
    name: str = "Among Us: local kill cooldown",
) -> Recipe:
    """Persist the kill-cooldown recipe as a ``candidate``.

    If ``player_control_class_static_fields_rva`` is supplied the root
    is module-relative; otherwise it is a placeholder root that the
    resolver pass must replace with a measured value. Either way, the
    status starts at ``candidate`` — observation elevates to
    ``observed`` and three-restart runs elevate to ``restart_verified``.
    """
    if target_fingerprint != SUPPORTED_BUILD_FINGERPRINT:
        raise ValueError('Among Us field layout is not qualified for this build fingerprint')
    if player_control_class_static_fields_rva is None:
        root = "placeholder"
    else:
        root = f"mod:{module_name}+0x{player_control_class_static_fields_rva:x}"

    ms = MappingStore(store)
    recipe = ms.save_new(
        name=name,
        target_fingerprint=target_fingerprint,
        value_type=KILL_TIMER_VALUE_TYPE,
        root=root,
        steps=[
            # LocalPlayer* at static_fields[0].
            {"op": "deref", "offset": f"0x{LOCAL_PLAYER_STATIC_OFFSET:x}", "width": 8},
            # killTimer at instance +0xB0.
            {"op": "add", "offset": f"0x{KILL_TIMER_INSTANCE_OFFSET:x}", "width": 4},
        ],
        preconditions=[
            "same_process_birth",
            "local_player_pointer_nonnull",
            "kill_timer_within_0_to_120_seconds",
        ],
        evidence_ids=[
            "p02-static:AmongUs/Assembly-CSharp/PlayerControl.cs:684 (killTimer offset 0xB0)",
            "p02-static:AmongUs/Assembly-CSharp/PlayerControl.cs:649 (LocalPlayer static offset 0x0)",
        ],
    )
    return recipe


def plausible_kill_timer(value: float) -> bool:
    """Shape filter: an active kill timer is a non-negative float <= 120s."""
    return isinstance(value, float) and 0.0 <= value <= 120.0


def scan_for_local_player_candidates(adapter, *, regions=None, kill_cooldown_setting=None,
                                     max_candidates=64, cancel=None, deadline_s=120.0):
    """Return bounded diagnostic candidates. These are never permission to write."""
    from .scans import ScanSession
    from .pointers import PointerPaths
    if not 1 <= max_candidates <= 1024 or not 0 < deadline_s <= 120:
        raise ValueError('invalid candidate search budget')
    regions = list(adapter.regions_writable() if regions is None else regions)
    candidates = PointerPaths()
    seen = set()
    scanner = ScanSession(adapter, 'u64')
    deadline = time.monotonic() + deadline_s
    for region_base, chunk, base in scanner._iter_chunks(regions, overlap=7):
        for offset in range((region_base-base) % 8, len(chunk)-7, 8):
            if (cancel and cancel()) or time.monotonic() >= deadline:
                candidates.complete = False
                candidates.reason = 'cancelled_or_time_budget'
                candidates.skipped_regions = scanner.skipped_regions
                return candidates
            ptr = struct.unpack_from('<Q', chunk, offset)[0]
            if not ptr or ptr % 8 or ptr in seen:
                continue
            address = ptr + KILL_TIMER_INSTANCE_OFFSET
            if not any(start <= ptr and address+4 <= start+size for start, size in regions):
                continue
            if len(seen) >= 65536:
                candidates.complete = False
                candidates.reason = 'candidate_budget'
                return candidates
            seen.add(ptr)
            try:
                value = struct.unpack('<f', adapter.read_bytes(address, 4))[0]
            except Exception:
                continue
            if plausible_kill_timer(value) and value > 0.05:
                candidates.append(LocalPlayerCandidate(ptr, value, 'unverified candidate; requires identity evidence'))
                if len(candidates) >= max_candidates:
                    candidates.complete = False
                    candidates.reason = 'candidate_limit'
                    candidates.skipped_regions = scanner.skipped_regions
                    return candidates
    candidates.skipped_regions = scanner.skipped_regions
    candidates.complete = not candidates.skipped_regions
    return candidates


AMONG_US_PROCESS_NAME = "AmongUs"
AMONG_US_INSTALL_PATH = "/Applications/AmongUs.app/Wrapper/AmongUs.app/AmongUs"


def find_among_us_pid() -> Optional[int]:
    """Return the current Among Us PID, or ``None`` if the game is not running.

    Uses ``pgrep -x`` so we match ``AmongUs`` exactly, not helpers.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-x", AMONG_US_PROCESS_NAME],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    pids = [int(line.strip()) for line in out.stdout.splitlines() if line.strip().isdigit()]
    if len(pids) > 1:
        raise ValueError('multiple Among Us processes are running; select one PID')
    return pids[0] if pids else None


def measure_live_build_fingerprint(install_path: str = AMONG_US_INSTALL_PATH) -> Optional[str]:
    """SHA-256 the main executable on disk so the live run binds to a
    measured build. Returns ``None`` when the install is absent."""
    import hashlib
    from pathlib import Path
    p = Path(install_path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_module_base_lookup(adapter):
    from .live_target import module_lookup
    return module_lookup(adapter)


def observe_kill_timer_over_time(
    adapter, address: int, *, duration_s: float = 2.0,
    sample_interval_s: float = 0.1,
) -> list[tuple[float, float]]:
    """Read-only sample of ``killTimer`` at ``address`` over time.

    Used both to validate candidates (a real killTimer either holds
    at the current cooldown or counts down) and to observe kill
    cycles during the live gate.
    """
    import struct
    import time
    out: list[tuple[float, float]] = []
    t_end = time.monotonic() + duration_s
    while time.monotonic() < t_end:
        try:
            raw = adapter.read_bytes(address, 4)
            val = struct.unpack("<f", raw)[0]
        except Exception:
            val = float("nan")
        out.append((time.monotonic(), val))
        time.sleep(sample_interval_s)
    return out


def validate_candidates_read_only(
    adapter, candidates: list[LocalPlayerCandidate],
    *, duration_s: float = 2.5, cooldown_hint: Optional[float] = None,
) -> list[LocalPlayerCandidate]:
    """Retain only finite, decreasing, roughly wall-clock-rate timer samples.

    These are diagnostic candidates, not proof that an object is LocalPlayer.
    Constant values and any unreadable sample are rejected.
    """
    survivors: list[LocalPlayerCandidate] = []
    for cand in candidates:
        samples = observe_kill_timer_over_time(
            adapter, cand.address + KILL_TIMER_INSTANCE_OFFSET,
            duration_s=duration_s, sample_interval_s=0.1,
        )
        # A constant zero or random positive scalar is not countdown evidence.
        if len(samples) < 5 or any(not math.isfinite(v) or not plausible_kill_timer(v) for _, v in samples):
            continue
        elapsed = samples[-1][0] - samples[0][0]
        values = [v for _, v in samples]
        drop = values[0] - values[-1]
        non_increasing = all(a >= b - 0.05 for a, b in zip(values, values[1:]))
        if elapsed > 0 and non_increasing and 0.4 * elapsed <= drop <= 1.6 * elapsed and drop >= 0.25:
            cand.reason = f'unverified identity; observed countdown {values[0]:.2f} -> {values[-1]:.2f}'
            survivors.append(cand)

    return survivors


def observe_kill_cycles(
    adapter, address: int, *, duration_s: float = 90.0,
    cooldown_hint: Optional[float] = None, sample_interval_s: float = 0.2,
) -> dict:
    """Detect multiple kill cycles by watching killTimer over a long window.

    A cycle is: timer snaps from ~0 up to the cooldown setting (or
    higher), then counts down back towards 0.

    Returns a summary suitable for the live gameplay report:
      * ``cycle_events`` — timestamp + approximate cooldown setting
        of every detected snap.
      * ``min_value`` / ``max_value`` observed.
      * ``samples`` — per-sample (t_rel, value).
    """
    import time
    samples = observe_kill_timer_over_time(
        adapter, address, duration_s=duration_s, sample_interval_s=sample_interval_s,
    )
    if not samples:
        return {"samples": [], "cycle_events": [], "min_value": None, "max_value": None}

    rel = [(t - samples[0][0], v) for t, v in samples]
    values = [v for _, v in rel if not (v != v)]
    cycles: list[dict] = []
    for i in range(1, len(rel)):
        prev_v = rel[i - 1][1]
        cur_v = rel[i][1]
        if (prev_v != prev_v) or (cur_v != cur_v):
            continue
        # Snap detection: value increases by at least 3.0 between
        # consecutive samples (a kill event resets the timer).
        if cur_v - prev_v >= 3.0:
            cycles.append({
                "t_s": rel[i][0], "before": prev_v, "after": cur_v,
                "matches_hint": (cooldown_hint is not None
                                 and abs(cur_v - cooldown_hint) <= 1.0),
            })
    return {
        "samples": rel,
        "cycle_events": cycles,
        "min_value": min(values) if values else None,
        "max_value": max(values) if values else None,
        "duration_s": duration_s,
    }


__all__ = [
    "AMONG_US_PROCESS_NAME", "AMONG_US_INSTALL_PATH",
    "LOCAL_PLAYER_STATIC_OFFSET", "KILL_TIMER_INSTANCE_OFFSET",
    "KILL_TIMER_VALUE_TYPE", "DEFAULT_KILL_COOLDOWN_SETTINGS",
    "LocalPlayerCandidate", "build_kill_cooldown_recipe",
    "plausible_kill_timer", "scan_for_local_player_candidates",
    "find_among_us_pid", "measure_live_build_fingerprint",
    "build_module_base_lookup", "observe_kill_timer_over_time",
    "validate_candidates_read_only", "observe_kill_cycles",
]
