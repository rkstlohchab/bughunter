"""Bounded discovery RPCs, with subscriber-owned state and out-of-band cancel."""
from __future__ import annotations

import math
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..game_runtime.scans import ScanBudgetExhausted, ScanCancelled, ScanSession, _SCALAR_FMT
from .protocol import MemoryEngineError


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MemoryEngineError(f"{name} must be an integer in {low}..{high}")
    return value


def _address(value):
    if not isinstance(value, str):
        raise MemoryEngineError("wire addresses must be decimal or 0x-prefixed strings")
    try:
        address = int(value, 16 if value.startswith("0x") else 10)
    except ValueError as exc:
        raise MemoryEngineError("invalid wire address") from exc
    if not 0 < address < 2**64:
        raise MemoryEngineError("address outside nonzero uint64 range")
    return address


def _regions(adapter, supplied):
    rows = adapter.regions_writable() if supplied is None else supplied
    if not isinstance(rows, (list, tuple)) or len(rows) > 65536:
        raise MemoryEngineError("regions must be a measured list with at most 65536 entries")
    regions = []
    for row in rows:
        if isinstance(row, dict):
            base, size = _address(row.get("base")), row.get("size")
        elif supplied is None and isinstance(row, (list, tuple)) and len(row) == 2:
            base, size = row
        else:
            raise MemoryEngineError("region requires base address and size")
        size = _integer(size, "region size", 1, 2**64 - 1)
        if not isinstance(base, int) or not 0 < base < 2**64 - size:
            raise MemoryEngineError("invalid memory region range")
        regions.append((base, size))
    regions.sort()
    if any(base < old_base + old_size for (old_base, old_size), (base, _) in zip(regions, regions[1:])):
        raise MemoryEngineError("overlapping scan regions")
    return regions


def _value(value):
    if isinstance(value, bytes):
        return value.hex()
    return None if isinstance(value, float) and not math.isfinite(value) else value


@dataclass
class _Entry:
    scan: ScanSession
    generation: int = 0
    reason: str = ""


class _BoundedAdapter:
    """Apply one deadline/byte budget across a multi-pass pointer discovery."""
    def __init__(self, adapter, cancel, seconds, byte_budget):
        self._adapter, self._cancel = adapter, cancel
        self._deadline = time.monotonic() + seconds
        self._budget, self.bytes_read = byte_budget, 0
        self.reason = ""
        self.scan_chunk_bytes = min(256 * 1024, byte_budget)

    def check(self):
        if self._cancel.is_set():
            self.reason = "cancelled"
            raise ScanCancelled("discovery cancelled")
        if time.monotonic() >= self._deadline:
            self.reason = "time_budget_exhausted"
            raise ScanBudgetExhausted("discovery time budget exhausted")

    def read_bytes(self, address, size):
        self.check()
        if self.bytes_read + size > self._budget:
            self.reason = "budget_exhausted"
            raise ScanBudgetExhausted("discovery read budget exhausted")
        raw = self._adapter.read_bytes(address, size)
        self.bytes_read += len(raw)
        self.check()
        return raw

    def regions_writable(self):
        self.check()
        return self._adapter.regions_writable()

    @property
    def identity(self):
        return self._adapter.identity


class DiscoveryService:
    def __init__(self, config):
        self._config = config
        self._lock = threading.RLock()
        self._scans: dict[tuple[str, str, str], _Entry] = {}
        self._active: dict[tuple[str, str, str], threading.Event] = {}

    def _owner(self, params):
        executor = str(params["session_id"])
        task = params.get("task_session_id")
        if not isinstance(task, str) or not task or len(task) > 256:
            raise MemoryEngineError("task_session_id is required for subscriber isolation")
        return executor, task

    def cancel_work(self, *, session_id=None, task_session_id=None, scan_id=None):
        """Never acquires the dispatcher lock; usable while a scan owns that lock."""
        with self._lock:
            events = [event for (executor, task, ident), event in self._active.items()
                      if (session_id is None or executor == session_id)
                      and (task_session_id is None or task == task_session_id)
                      and (scan_id is None or ident == scan_id)]
            for event in events:
                event.set()
        return {"status": "cancel_requested", "cancelled_work": len(events)}

    def close_owner(self, session_id, task_session_id=None):
        self.cancel_work(session_id=session_id, task_session_id=task_session_id)
        with self._lock:
            keys = [key for key in self._scans if key[0] == session_id
                    and (task_session_id is None or key[1] == task_session_id)]
            for key in keys:
                self._scans.pop(key).scan.close()

    def _budget(self, params):
        seconds = params.get("deadline_s", 15.0)
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not 0 < seconds <= 20:
            raise MemoryEngineError("deadline_s must be in (0, 20] seconds")
        byte_budget = _integer(params.get("max_scan_bytes", self._config.max_scan_bytes),
                               "max_scan_bytes", 1, self._config.max_scan_bytes)
        return float(seconds), byte_budget

    def _page(self, key, entry, params):
        count = _integer(params.get("max_results", 100), "max_results", 1, 200)
        cursor = params.get("cursor")
        offset = 0
        if cursor is not None:
            try:
                token, generation, offset_text = str(cursor).split(":")
                offset = int(offset_text)
                if token != key[2] or int(generation) != entry.generation or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise MemoryEngineError("invalid or stale scan cursor") from exc
        scan = entry.scan
        if offset > len(scan.matches):
            raise MemoryEngineError("cursor exceeds scan result count")
        page = scan.matches[offset:offset + count]
        following = offset + len(page)
        complete = scan.progress.phase == "complete" and not entry.reason
        return {"status": scan.progress.phase, "scan_id": key[2], "value_type": scan.value_type,
                "progress": scan.progress.as_dict(), "complete": complete, "partial": not complete,
                "reason": entry.reason or scan._incomplete_reason or ("partial_read" if scan.skipped_regions else ""),
                "total_matches": len(scan.matches), "snapshot_bytes": scan._snapshot_bytes,
                "cursor": cursor, "next_cursor": f"{key[2]}:{entry.generation}:{following}"
                if following < len(scan.matches) else None,
                "results": [{"address": hex(m.address), "value": _value(m.value),
                             "value_type": m.value_type} for m in page],
                "skipped_regions": [{**row, "base": hex(row["base"])} for row in scan.skipped_regions[:200]],
                "skipped_regions_total": scan._skipped_count}

    def scan(self, adapter, params):
        owner = self._owner(params)
        mode = params.get("scan_mode", "exact")
        _integer(params.get("max_results", 100), "max_results", 1, 200)
        if mode not in {"exact", "range", "unknown_initial", "byte_pattern", "refine", "results", "close"}:
            raise MemoryEngineError("unsupported scan_mode")
        scan_id = params.get("scan_id")
        if scan_id is not None and (not isinstance(scan_id, str) or not scan_id or ":" in scan_id or len(scan_id) > 128):
            raise MemoryEngineError("invalid scan_id")
        scan_id = scan_id or uuid.uuid4().hex
        key = (*owner, scan_id)
        created = False
        with self._lock:
            entry = self._scans.get(key)
            if mode == "close":
                if entry is not None:
                    self._scans.pop(key).scan.close()
                return {"status": "closed" if entry else "unknown", "scan_id": scan_id}
            if mode in {"results", "refine"} and entry is None:
                raise MemoryEngineError("unknown scan_id for this subscriber")
            if mode == "results":
                return self._page(key, entry, params)
            if entry is None:
                if len(self._scans) >= 16 or sum(k[:2] == owner for k in self._scans) >= 4:
                    raise MemoryEngineError("scan session capacity exhausted; close unused scans")
                value_type = params.get("value_type", "bytes" if mode == "byte_pattern" else "u32")
                if value_type not in {*_SCALAR_FMT, "bytes"}:
                    raise MemoryEngineError("unsupported value_type")
                entry = _Entry(ScanSession(adapter, value_type, session_id=scan_id))
                self._scans[key] = entry
                created = True
            elif params.get("value_type", entry.scan.value_type) != entry.scan.value_type:
                raise MemoryEngineError("scan value_type cannot change; use a new scan_id")
            entry.generation += 1
            entry.reason = ""
            entry.scan._cancel.clear()
            self._active[key] = entry.scan._cancel
        try:
            seconds, byte_budget = self._budget(params)
            # Completed scans retain candidates. Account for other subscribers
            # before reserving the remaining shared resident match budget.
            with self._lock:
                other_match_bytes = sum(len(other.scan.matches) * (192 + (other.scan._pattern_size if other.scan.value_type == "bytes" else 0))
                                        for other in self._scans.values()
                                        if other is not entry)
                match_width = 192 + ((entry.scan._pattern_size or 4096) if entry.scan.value_type == "bytes" else 0)
                existing_bytes = len(entry.scan.matches) * match_width if mode == "refine" else 0
                if mode == "unknown_initial":
                    snapshot_available = 2 * 1024 * 1024 * 1024 - sum(
                        other.scan._snapshot_bytes for other in self._scans.values() if other is not entry)
                    if snapshot_available <= 0:
                        raise ScanBudgetExhausted("shared snapshot budget exhausted")
                    byte_budget = min(byte_budget, snapshot_available)
            match_budget = min(self._config.max_scan_results,
                               (256 * 1024 * 1024 - other_match_bytes - existing_bytes - 12 * 1024 * 1024) // match_width)
            if match_budget <= 0:
                raise ScanBudgetExhausted("shared resident scan budget exhausted")
            entry.scan.set_limits(max_read_bytes=byte_budget, max_matches=self._config.max_scan_results,
                                  deadline_s=seconds, chunk_bytes=min(256 * 1024, byte_budget))
            entry.scan.max_matches = match_budget
            if mode == "refine":
                needle = params.get("needle")
                if entry.scan.value_type == "bytes" and params.get("byte_pattern_hex") is not None:
                    needle = bytes.fromhex(params["byte_pattern_hex"])
                entry.scan.refine(params.get("refinement", "changed"), needle=needle)
            else:
                regions = _regions(adapter, params.get("regions"))
                if mode == "exact":
                    entry.scan.scan_exact(params.get("needle"), regions=regions)
                elif mode == "range":
                    entry.scan.scan_range(params.get("low"), params.get("high"), regions=regions)
                elif mode == "unknown_initial":
                    entry.scan.scan_unknown_initial(regions=regions)
                else:
                    pattern = bytes.fromhex(params.get("byte_pattern_hex", ""))
                    entry.scan.scan_exact(None, byte_pattern=pattern, regions=regions)
        except (ScanBudgetExhausted, ScanCancelled) as exc:
            entry.reason = str(exc)
            if entry.scan.progress.phase not in {"cancelled", "time_budget_exhausted", "budget_exhausted"}:
                entry.scan.progress.phase = "budget_exhausted"
            entry.scan._incomplete_reason = entry.scan.progress.phase
        except MemoryEngineError:
            if created:
                with self._lock:
                    self._scans.pop(key, None)
                entry.scan.close()
            raise
        except (ValueError, TypeError, KeyError, struct.error) as exc:
            entry.reason = "invalid_request"
            entry.scan.progress.phase = "error"
            if created:
                with self._lock:
                    self._scans.pop(key, None)
                entry.scan.close()
            raise MemoryEngineError(f"invalid scan request: {exc}") from exc
        finally:
            with self._lock:
                self._active.pop(key, None)
        return self._page(key, entry, params)

    def pointer_search(self, adapter, params):
        from ..game_runtime.pointers import discover_pointer_paths
        from ..game_runtime.among_us import scan_for_local_player_candidates, SUPPORTED_BUILD_FINGERPRINT

        owner = self._owner(params)
        search_id = str(params.get("scan_id") or uuid.uuid4().hex)
        key = (*owner, search_id)
        seconds, byte_budget = self._budget(params)
        max_results = _integer(params.get("max_results", params.get("max_candidates", 100)), "max_results", 1, 200)
        mode = params.get("search_mode", "pointer_paths")
        event = threading.Event()
        bounded = _BoundedAdapter(adapter, event, seconds, byte_budget)
        with self._lock:
            self._active[key] = event
        result, reason, complete = [], "", False
        skipped = []
        try:
            regions = _regions(bounded, params.get("regions"))
            def cancel():
                # The legacy discovery routines consult this in their inner loops.
                return event.is_set() or bool(bounded.reason) or time.monotonic() >= bounded._deadline
            if mode == "among_us_candidates":
                from ..game_runtime.live_target import process_image
                try:
                    _, image_fingerprint, process_birth = process_image(adapter.identity.pid)
                except (OSError, ValueError) as exc:
                    raise MemoryEngineError(f"diagnostic image fingerprint could not be measured: {exc}") from exc
                if process_birth != adapter.identity.start_time_ns:
                    raise MemoryEngineError("diagnostic process birth identity changed")
                qualified = params.get("qualified_image_fingerprint")
                if qualified is not None and qualified != image_fingerprint:
                    raise MemoryEngineError("diagnostic image fingerprint differs from the qualified image")
                if image_fingerprint != SUPPORTED_BUILD_FINGERPRINT:
                    raise MemoryEngineError("Among Us diagnostic layout is not qualified for this build fingerprint")
                paths = scan_for_local_player_candidates(bounded, regions=regions,
                            max_candidates=max_results, cancel=cancel, deadline_s=seconds)
                result = [{"address": hex(row.address), "kill_timer": row.kill_timer,
                           "reason": row.reason, "verification_status": "candidate"} for row in paths]
            elif mode == "pointer_paths":
                modules = self._module_ranges(adapter, params.get("module_ranges"))
                paths = discover_pointer_paths(bounded, target_address=_address(params.get("target_address")),
                            depth=_integer(params.get("depth", 3), "depth", 1, 12),
                            offset_window=_integer(params.get("offset_window", 4096), "offset_window", 0, 4096),
                            regions=regions, module_ranges=modules, max_candidates=max_results,
                            cancel=cancel, deadline_s=seconds)
                result = [{"root": row.root, "steps": row.steps, "depth": row.depth,
                           "verification_status": "candidate"} for row in paths]
            else:
                raise MemoryEngineError("unsupported pointer search_mode")
            complete, reason = paths.complete, paths.reason
            skipped = paths.skipped_regions
        except (ScanBudgetExhausted, ScanCancelled) as exc:
            reason = bounded.reason or str(exc)
        except (ValueError, TypeError) as exc:
            raise MemoryEngineError(f"invalid pointer search request: {exc}") from exc
        finally:
            with self._lock:
                self._active.pop(key, None)
        if event.is_set():
            reason, complete = "cancelled", False
        elif bounded.reason:
            reason, complete = bounded.reason, False
        elif time.monotonic() >= bounded._deadline:
            reason, complete = "time_budget_exhausted", False
        return {"status": "complete" if complete else "partial", "scan_id": search_id,
                "search_mode": mode, "complete": complete, "partial": not complete,
                "reason": reason, "results": result, "total_matches": len(result),
                "progress": {"bytes_read": bounded.bytes_read, "phase": "complete" if complete else "partial"},
                "skipped_regions": [{**row, "base": hex(row["base"])} for row in skipped[:200]]}

    @staticmethod
    def _module_ranges(adapter, supplied):
        """Use observed module extents only; a base alone does not establish size."""
        modules = getattr(adapter, "modules", lambda: [])() if supplied is None else supplied
        if not isinstance(modules, (list, tuple)) or len(modules) > 4096:
            raise MemoryEngineError("module_ranges must be a measured list")
        result = []
        for row in modules:
            try:
                base = _address(row["base"]) if isinstance(row["base"], str) else int(row["base"])
                size = int(row.get("size", 0))
                name = str(row.get("name") or Path(row["path"]).name)
            except (KeyError, ValueError, TypeError):
                continue
            if base > 0 and size > 0 and base + size <= 2**64:
                result.append((name, base, size))
        return result
