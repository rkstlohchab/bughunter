"""Read-only analysis lane of the private helper: engine probes and live sampling.

Requests reach this lane as broker ``read`` operations carrying
``analysis_action``. Every action is bounded by a deadline (at most 15 s,
inside the desktop executor's 30 s helper wait) and a byte budget, and
registers a cancel event with the discovery service so quiesce/Stop
interrupts it like a scan. Probe jobs and snapshots are owned by
(executor session, task session) and released with the task.

Sampling never holds a debugger transaction across the whole window: each
sample is one read (or one short transaction for several addresses) and the
target runs between samples. The achieved rate is computed from measured
timestamps.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from contextlib import nullcontext

from ..game_runtime import live_analysis
from ..game_runtime.engine_map.detect import detect_engine
from ..game_runtime.engine_map.il2cpp_runtime import (
    Il2CppClassProbe,
    Il2CppFieldProbe,
    Il2CppInstanceProbe,
)
from ..game_runtime.engine_map.memview import (
    is_identifier,
    module_ranges,
    normalise_regions,
    read_cstring,
    read_u64,
    readable_regions,
    safe_read,
)
from ..game_runtime.engine_map.unreal_runtime import (
    NameLayout,
    UnrealNamesProbe,
    UnrealObjectsProbe,
)
from ..game_runtime.scans import ScanBudgetExhausted, ScanCancelled
from .discovery import _BoundedAdapter
from .protocol import MemoryEngineError

MAX_DEADLINE_S = 15.0
MAX_WINDOW = 4096
MAX_DISSECT = 1024
MAX_ADDRESSES = 64
MAX_JOBS_PER_TASK = 4
MAX_SNAPSHOTS_PER_TASK = 8
_SYSTEM_PREFIXES = ("/System/", "/usr/lib/", "/Library/Apple/", "/usr/libexec/")
PROBES = ("il2cpp_class", "il2cpp_instances", "il2cpp_field", "unreal_names", "unreal_objects")


def _address(value, name="address") -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", value):
        raise MemoryEngineError(f"{name} must be a 0x-prefixed hexadecimal string")
    address = int(value, 16)
    if not 0 < address < 2**64:
        raise MemoryEngineError(f"{name} outside nonzero uint64 range")
    return address


def _number(value, name, low, high, default):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise MemoryEngineError(f"{name} must be in {low}..{high}")
    return value


def _system_ranges(adapter) -> list[tuple[int, int]]:
    getter = getattr(adapter, "modules", None)
    out = []
    if callable(getter):
        for row in getter() or []:
            path = str(row.get("path") or "")
            if path.startswith(_SYSTEM_PREFIXES):
                try:
                    raw = row["base"]
                    base = int(raw, 0) if isinstance(raw, str) else int(raw)
                    size = int(row.get("size", 0))
                except (KeyError, TypeError, ValueError):
                    continue
                if base > 0 and size > 0:
                    out.append((base, size))
    return out


def _subtract(regions, excluded):
    """Drop regions entirely inside an excluded (system library) range."""
    return [(b, s) for b, s in regions if not any(eb <= b and b + s <= eb + es for eb, es in excluded)]


class AnalysisOperations:
    def __init__(self, server):
        self.server = server
        self._lock = threading.Lock()
        self.jobs: dict[tuple[str, str, str], object] = {}
        self.snapshots: dict[tuple[str, str, str], dict] = {}

    # ------------------------------------------------------------------ plumbing

    def _key(self, params, ident):
        task = params.get("task_session_id")
        if not isinstance(task, str) or not task:
            raise MemoryEngineError("task_session_id is required")
        return (str(params["session_id"]), task, ident)

    def _bounded(self, adapter, params, key):
        seconds = float(_number(params.get("deadline_s"), "deadline_s", 0.1, MAX_DEADLINE_S, 12.0))
        budget = int(_number(params.get("max_scan_bytes"), "max_scan_bytes", 1, self.server.config.max_scan_bytes,
                             self.server.config.max_scan_bytes))
        event = threading.Event()
        discovery = self.server.discovery
        with discovery._lock:
            discovery._active[key] = event
        return _BoundedAdapter(adapter, event, seconds, budget), event

    def _release(self, key):
        discovery = self.server.discovery
        with discovery._lock:
            discovery._active.pop(key, None)

    def _regions(self, adapter, params):
        supplied = params.get("regions")
        if supplied is not None:
            regions = normalise_regions(supplied)
            return regions, regions
        system = _system_ranges(adapter)
        readable = _subtract(readable_regions(adapter), system)
        writable = _subtract(normalise_regions(adapter.regions_writable()), system)
        return readable, writable

    def _modules(self, adapter, params):
        supplied = params.get("module_ranges")
        if supplied is not None:
            return [(str(r["name"]), int(r["base"], 16), int(r["size"])) for r in supplied]
        return module_ranges(adapter)

    # ------------------------------------------------------------------ dispatch

    def dispatch(self, params):
        from .server import _identity_from
        session = self.server._session(params["session_id"], _identity_from(params))
        action = params.get("analysis_action")
        handler = {
            "engine_detect": self._engine_detect, "probe": self._probe, "probe_close": self._probe_close,
            "watch": self._watch, "dissect": self._dissect, "snapshot": self._snapshot, "diff": self._diff,
        }.get(action)
        if handler is None:
            raise MemoryEngineError(f"unknown analysis_action: {action!r}")
        return handler(session.adapter, params)

    def _engine_detect(self, adapter, params):
        modules = []
        getter = getattr(adapter, "modules", None)
        if callable(getter):
            modules = getter() or []
        bundle = params.get("bundle_path")
        detection = detect_engine(modules, bundle if isinstance(bundle, str) and bundle else None)
        ranges = module_ranges(adapter)
        interesting = [r for r in ranges if not any(str(m.get("path", "")).startswith(_SYSTEM_PREFIXES)
                                                     and str(m.get("path", "")).endswith(r[0]) for m in modules)]
        return {"engine": detection, "modules": [{"name": n, "base": hex(b), "size": hex(s)} for n, b, s in interesting[:64]],
                "module_count": len(ranges)}

    def _probe(self, adapter, params):
        job_id = params.get("job_id")
        if job_id is not None:
            key = self._key(params, str(job_id))
            with self._lock:
                job = self.jobs.get(key)
            if job is None:
                raise MemoryEngineError("unknown probe job for this task; start a new probe")
        else:
            kind = params.get("probe")
            if kind not in PROBES:
                raise MemoryEngineError(f"probe must be one of {PROBES}")
            readable, writable = self._regions(adapter, params)
            modules = self._modules(adapter, params)
            try:
                if kind == "il2cpp_class":
                    job = Il2CppClassProbe(params.get("name"), readable, writable, namespace=params.get("namespace"))
                elif kind == "il2cpp_instances":
                    job = Il2CppInstanceProbe(_address(params.get("class_address"), "class_address"), writable)
                elif kind == "il2cpp_field":
                    job = Il2CppFieldProbe(params.get("name"), _address(params.get("class_address"), "class_address"),
                                           readable, writable)
                elif kind == "unreal_names":
                    job = UnrealNamesProbe(readable, writable, modules)
                else:
                    layout = params.get("name_layout")
                    if not isinstance(layout, dict):
                        raise MemoryEngineError("unreal_objects needs name_layout from an unreal_names result")
                    job = UnrealObjectsProbe(NameLayout.from_wire(layout), writable, modules)
            except (ValueError, KeyError, TypeError) as exc:
                raise MemoryEngineError(f"invalid probe request: {exc}") from exc
            key = self._key(params, job.id)
            with self._lock:
                owned = [k for k in self.jobs if k[:2] == key[:2]]
                if len(owned) >= MAX_JOBS_PER_TASK:
                    raise MemoryEngineError("probe capacity for this task exhausted; close finished probes")
                self.jobs[key] = job
        bounded, event = self._bounded(adapter, params, key)
        reason = ""
        try:
            job.advance(bounded)
        except (ScanBudgetExhausted, ScanCancelled) as exc:
            reason = bounded.reason or str(exc)
        finally:
            self._release(key)
        status = job.status()
        status.update(complete=job.done, reason=reason, bytes_read_this_call=bounded.bytes_read,
                      findings=list(job.findings) if job.done else [])
        if job.done:
            with self._lock:
                self.jobs.pop(key, None)
        elif event.is_set():
            status["reason"] = "cancelled"
        return status

    def _probe_close(self, adapter, params):
        key = self._key(params, str(params.get("job_id")))
        with self._lock:
            job = self.jobs.pop(key, None)
        return {"closed": job is not None, "job_id": key[2]}

    def _targets(self, params):
        addresses = params.get("addresses")
        if addresses is not None:
            if not isinstance(addresses, list) or not 1 <= len(addresses) <= MAX_ADDRESSES:
                raise MemoryEngineError(f"addresses must list 1..{MAX_ADDRESSES} hexadecimal strings")
            return [(_address(a), 8) for a in addresses]
        size = int(_number(params.get("size"), "size", 4, MAX_WINDOW, 256))
        return [(_address(params.get("address")), size - size % 4)]

    def _watch(self, adapter, params):
        targets = self._targets(params)
        duration = float(_number(params.get("duration_s"), "duration_s", 0.2, 10.0, 3.0))
        interval = float(_number(params.get("interval_ms"), "interval_ms", 20, 2000, 100)) / 1000.0
        key = self._key(params, "watch:" + uuid.uuid4().hex)
        bounded, event = self._bounded(adapter, params, key)
        low = min(a for a, _ in targets)
        span = max(a + s for a, s in targets) - low
        # One read of a covering span is one consistent sample and one debugger round trip;
        # only addresses spread wider than a window need a per-sample transaction.
        covering = len(targets) > 1 and span <= MAX_WINDOW
        transaction = getattr(adapter, "transaction", None) if len(targets) > 1 and not covering else None
        samples: list[list[bytes]] = []
        times: list[float] = []
        reason = ""
        try:
            started = time.monotonic()
            while time.monotonic() - started <= duration and len(times) < 500:
                if event.is_set():
                    reason = "cancelled"
                    break
                tick = time.monotonic()
                if covering:
                    whole = bounded.read_bytes(low, span)
                    reads = [whole[address - low:address - low + size] for address, size in targets]
                else:
                    with (transaction() if transaction else nullcontext()):
                        reads = [bounded.read_bytes(address, size) for address, size in targets]
                if any(len(raw) != size for raw, (_, size) in zip(reads, targets)):
                    reason = "partial_read"
                    break
                samples.append(reads)
                times.append(time.monotonic())
                remaining = interval - (time.monotonic() - tick)
                if remaining > 0:
                    event.wait(remaining)
        except (ScanBudgetExhausted, ScanCancelled) as exc:
            reason = bounded.reason or str(exc)
        except Exception as exc:  # a failed read ends the window; earlier samples stay valid
            reason = f"read_failed: {type(exc).__name__}: {str(exc)[:200]}"
        finally:
            self._release(key)
        result = {"watch": True, "targets": [{"address": hex(a), "size": s} for a, s in targets],
                  "samples": len(times), "reason": reason, "complete": not reason, "findings": []}
        if len(times) < 2:
            result["complete"] = False
            result["reason"] = reason or "fewer than two samples"
            return result
        modules = self._modules(adapter, params)
        readable = normalise_regions(params["regions"]) if params.get("regions") is not None else None
        if readable is None:
            try:
                readable = readable_regions(adapter)
            except Exception:
                readable = []
        windows = []
        for index, (address, size) in enumerate(targets):
            series = [row[index] for row in samples]
            windows.append(live_analysis.classify(series, times, address, readable=readable, modules=modules))
        span = times[-1] - times[0]
        result.update(duration_s=round(span, 4), achieved_hz=round((len(times) - 1) / span, 2) if span > 0 else None,
                      windows=windows, last=[row.hex() for row in samples[-1]] if sum(s for _, s in targets) <= MAX_WINDOW else None)
        for window in windows:
            for row in window["rows"]:
                if row.get("label") in ("timer", "counter", "vector3_candidate") and len(result["findings"]) < 8:
                    value_type = {"i32": "i32", "f32": "f32", "f32x3": "f32"}.get(row["type"])
                    evidence = {k: row[k] for k in ("label", "rate_per_s", "r2", "steps", "changes", "first", "last") if k in row}
                    evidence.update(samples=len(times), achieved_hz=result["achieved_hz"])
                    result["findings"].append({"kind": "value_behavior",
                                               "title": f"{row['label'].replace('_', ' ')} at {row['address']}",
                                               "address": "abs:" + row["address"], "value_type": value_type,
                                               "evidence": evidence})
        return result

    def _annotate_il2cpp(self, reader, value, name_slot):
        klass = read_u64(reader, value)
        if not klass:
            return None
        name_pointer = read_u64(reader, klass + name_slot)
        text = read_cstring(reader, name_pointer) if name_pointer else None
        return text.decode("ascii") if is_identifier(text) else None

    def _dissect(self, adapter, params):
        address = _address(params.get("address"))
        size = int(_number(params.get("size"), "size", 8, MAX_DISSECT, 0x100))
        key = self._key(params, "dissect:" + uuid.uuid4().hex)
        bounded, _ = self._bounded(adapter, params, key)
        try:
            raw = safe_read(bounded, address, size - size % 8)
            if raw is None:
                raise MemoryEngineError(f"dissect read failed at 0x{address:x}")
            readable = normalise_regions(params["regions"]) if params.get("regions") is not None else readable_regions(adapter)
            rows = live_analysis.dissect(raw, address, readable=readable, modules=self._modules(adapter, params))
            name_slot = params.get("il2cpp_name_slot")
            if isinstance(name_slot, str) and re.fullmatch(r"0x[0-9a-fA-F]{1,3}", name_slot):
                labelled = 0
                for row in rows:
                    if "pointer" in row and labelled < 32:
                        labelled += 1
                        name = self._annotate_il2cpp(bounded, int(row["u64"], 16), int(name_slot, 16))
                        if name:
                            row["il2cpp_class"] = name
                head = self._annotate_il2cpp(bounded, address, int(name_slot, 16))
            else:
                head = None
        except (ScanBudgetExhausted, ScanCancelled) as exc:
            raise MemoryEngineError(f"dissect interrupted: {exc}") from exc
        finally:
            self._release(key)
        return {"address": hex(address), "size": len(raw), "object_class": head, "rows": rows}

    def _snapshot(self, adapter, params):
        (address, size), = self._targets({**params, "addresses": None})
        key_owner = self._key(params, "")
        with self._lock:
            owned = [k for k in self.snapshots if k[:2] == key_owner[:2]]
            if len(owned) >= MAX_SNAPSHOTS_PER_TASK:
                oldest = min(owned, key=lambda k: self.snapshots[k]["time"])
                self.snapshots.pop(oldest)
        raw = safe_read(adapter, address, size)
        if raw is None:
            raise MemoryEngineError(f"snapshot read failed at 0x{address:x}")
        snap_id = uuid.uuid4().hex
        with self._lock:
            self.snapshots[self._key(params, snap_id)] = {"address": address, "raw": raw, "time": time.monotonic()}
        return {"snapshot_id": snap_id, "address": hex(address), "size": size}

    def _diff(self, adapter, params):
        key = self._key(params, str(params.get("snapshot_id")))
        with self._lock:
            snap = self.snapshots.get(key)
        if snap is None:
            raise MemoryEngineError("unknown snapshot for this task")
        raw = safe_read(adapter, snap["address"], len(snap["raw"]))
        if raw is None:
            raise MemoryEngineError("diff read failed")
        rows = live_analysis.diff(snap["raw"], raw, snap["address"])
        elapsed = time.monotonic() - snap["time"]
        if params.get("replace", True):
            with self._lock:
                self.snapshots[key] = {"address": snap["address"], "raw": raw, "time": time.monotonic()}
        return {"snapshot_id": key[2], "address": hex(snap["address"]), "elapsed_s": round(elapsed, 3),
                "changed": len(rows), "rows": rows}

    # ------------------------------------------------------------------ lifecycle

    def release_task(self, task):
        with self._lock:
            for store in (self.jobs, self.snapshots):
                for key in [k for k in store if k[1] == task]:
                    store.pop(key, None)

    def close(self):
        with self._lock:
            self.jobs.clear()
            self.snapshots.clear()


__all__ = ["PROBES", "AnalysisOperations"]
