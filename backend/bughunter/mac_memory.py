"""Legacy facade for the macOS memory engine.

This module used to be a full ``mach_vm`` + scanner implementation. The
reusable primitives now live in :mod:`bughunter.memory_engine` and this
file keeps the old public API (``MacMemory`` with ``list_processes``,
``attach``, ``read_value``, ``write_value``, ``scan``, ``next_scan``,
``results``, ``freeze``, ``modules``, ``resolve_chain``, ``detach``,
``shutdown``) so existing callers in the app continue to work without
change.

All read/write/pointer-walk paths delegate to
:class:`bughunter.memory_engine.mac_adapter.MacMemoryAdapter`. The
scanner is retained here because the engine side exposes it as a
not-yet-implemented placeholder (see P05); keeping it here avoids a
regression for the legacy UI.
"""
from __future__ import annotations

import ctypes
import struct
import sys
import threading
from functools import wraps
from ctypes import (
    POINTER,
    byref,
    c_int,
    c_size_t,
    c_uint,
    c_ulonglong,
    c_void_p,
    create_string_buffer,
)

# Absolute imports so the facade also works when loaded via
# ``importlib.util.spec_from_file_location`` (legacy test harness).
from bughunter.memory_engine.mac_adapter import MacMemoryAdapter, _SCALAR_FMT
from bughunter.memory_engine.protocol import (
    MemoryEngineError,
    PartialReadError,
    ProcessIdentity,
)


# ``MemoryError_`` was the legacy name for the error type; existing
# callers import it. Keep it as an alias.
class MemoryError_(Exception):
    pass


# Legacy public constants (used by the operator UI code).
TYPES = {
    "int": ("<i", 4), "uint": ("<I", 4), "int16": ("<h", 2), "int64": ("<q", 8),
    "uint64": ("<Q", 8), "float": ("<f", 4), "double": ("<d", 8), "byte": ("<b", 1),
}
MAX_RESULTS = 500_000
MAX_SCAN_BYTES = 3 * 1024 ** 3


# ---------------------------------------------------------------------------
# Process listing (no attach required)
# ---------------------------------------------------------------------------


def _libc_for_listing():
    if sys.platform != "darwin":
        return None
    try:
        return ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    except OSError:
        return None


def _list_processes(filter: str | None = None) -> list[dict]:
    libc = _libc_for_listing()
    if libc is None:
        return []
    libc.proc_listpids.argtypes = [c_uint, c_uint, c_void_p, c_int]
    libc.proc_listpids.restype = c_int
    libc.proc_name.argtypes = [c_int, c_void_p, c_uint]
    libc.proc_name.restype = c_int
    PROC_ALL_PIDS = 1
    count = libc.proc_listpids(PROC_ALL_PIDS, 0, None, 0)
    if count <= 0:
        return []
    n = count // 4
    buf = (c_int * (n + 64))()
    got = libc.proc_listpids(PROC_ALL_PIDS, 0, buf, ctypes.sizeof(buf))
    pids = [buf[i] for i in range(got // 4) if buf[i] > 0]
    out: list[dict] = []
    namebuf = create_string_buffer(256)
    for pid in pids:
        namebuf.raw = b"\x00" * 256
        if libc.proc_name(pid, namebuf, 256) > 0:
            name = namebuf.value.decode("utf-8", "replace")
        else:
            name = ""
        if filter and filter.lower() not in name.lower():
            continue
        out.append({"pid": pid, "name": name})
    out.sort(key=lambda p: p["name"].lower())
    return out


# ---------------------------------------------------------------------------
# Session wrapper (kept for scanner state)
# ---------------------------------------------------------------------------


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class _Session:
    def __init__(self, pid: int, name: str, adapter: MacMemoryAdapter) -> None:
        self.pid = pid
        self.name = name
        self.adapter = adapter
        self.vtype = "int"
        self.results: list[int] = []
        self.snapshot: dict[int, bytes] = {}
        self.freezes: dict[int, threading.Event] = {}


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class MacMemory:
    """Legacy wrapper. Delegates read/write/pointer to the new adapter.

    The old scanner API (``scan``, ``next_scan``, ``results``) is kept
    inside this facade until the game runtime takes over scans (P05).
    """

    def __init__(self) -> None:
        self.available = sys.platform == "darwin"
        self._sessions: dict[int, _Session] = {}
        self._lock = threading.RLock()

    # ------------- process discovery -------------
    def list_processes(self, filter: str | None = None) -> list[dict]:
        if not self.available:
            raise MemoryError_("mac memory engine requires darwin")
        return _list_processes(filter)

    # ------------- attach / detach -------------
    @_serialized
    def attach(self, pid: int) -> dict:
        if not self.available:
            raise MemoryError_("mac memory engine requires darwin")
        name = next((p["name"] for p in self.list_processes() if p["pid"] == pid), str(pid))
        old = self._sessions.pop(pid, None)
        if old is not None:
            self._retire_session(old)
        try:
            adapter = MacMemoryAdapter(pid)
        except (MemoryEngineError, RuntimeError) as exc:
            raise MemoryError_(str(exc)) from exc
        self._sessions[pid] = _Session(pid, name, adapter)
        return {"pid": pid, "name": name, "attached": True}

    def detach(self, pid: int) -> dict:
        with self._lock:
            s = self._sessions.pop(pid, None)
            if s is not None:
                self._retire_session(s)
        return {"pid": pid, "attached": False}

    def _retire_session(self, session: _Session) -> None:
        for stop in session.freezes.values():
            stop.set()
        session.freezes.clear()
        try:
            session.adapter.close()
        except Exception:
            pass

    def _session(self, pid: int) -> _Session:
        s = self._sessions.get(pid)
        if s is None:
            raise MemoryError_(f"Not attached to pid {pid}; call memory.attach first.")
        return s

    # ------------- raw / typed value read/write -------------
    @_serialized
    def read_value(self, pid: int, address: int, vtype: str = "int"):
        s = self._session(pid)
        try:
            value = s.adapter.read_value(int(address), vtype)
        except MemoryEngineError as exc:
            raise MemoryError_(str(exc)) from exc
        return {"pid": pid, "address": hex(int(address)), "type": vtype, "value": value}

    @_serialized
    def write_value(self, pid: int, address: int, value, vtype: str = "int") -> dict:
        s = self._session(pid)
        try:
            s.adapter.write_value(int(address), value, vtype, verify=True)
        except MemoryEngineError as exc:
            raise MemoryError_(f"Write to {hex(int(address))} failed: {exc}") from exc
        return {"pid": pid, "address": hex(int(address)), "type": vtype, "written": value}

    # ------------- freeze (unchanged behaviour) -------------
    @_serialized
    def freeze(self, pid: int, address: int, value, vtype: str = "int", enable: bool = True) -> dict:
        s = self._session(pid)
        prev = s.freezes.pop(int(address), None)
        if prev is not None:
            prev.set()
        if not enable:
            return {"pid": pid, "address": hex(int(address)), "frozen": False}
        try:
            s.adapter.write_value(int(address), value, vtype, verify=True)
        except MemoryEngineError as exc:
            raise MemoryError_(f"Cannot freeze {hex(int(address))}: {exc}") from exc
        stop = threading.Event()
        s.freezes[int(address)] = stop
        adapter = s.adapter

        def hold():
            while not stop.wait(0.05):
                with self._lock:
                    if stop.is_set() or self._sessions.get(pid) is not s:
                        break
                    try:
                        adapter.write_value(int(address), value, vtype, verify=False)
                    except MemoryEngineError:
                        stop.set()
                        s.freezes.pop(int(address), None)
                        break
        threading.Thread(target=hold, daemon=True, name=f"freeze-{pid}-{int(address):x}").start()
        return {"pid": pid, "address": hex(int(address)), "type": vtype, "value": value, "frozen": True}

    # ------------- pointer chain -------------
    @_serialized
    def resolve_chain(self, pid: int, base: int, offsets: list[int], vtype: str = "int") -> dict:
        s = self._session(pid)
        addr = int(base)
        trail = [hex(addr)]
        try:
            for off in offsets[:-1]:
                raw = s.adapter.read_bytes(addr + int(off), 8)
                addr = struct.unpack("<Q", raw)[0]
                trail.append(hex(addr))
            final = addr + (int(offsets[-1]) if offsets else 0)
            fmt, size = _SCALAR_FMT[vtype]
            raw = s.adapter.read_bytes(final, size)
            value = struct.unpack(fmt, raw)[0]
        except MemoryEngineError as exc:
            raise MemoryError_(str(exc)) from exc
        return {
            "pid": pid,
            "final_address": hex(final),
            "trail": trail,
            "type": vtype,
            "value": value,
        }

    # ------------- modules (dyld_all_image_infos) -------------
    @_serialized
    def modules(self, pid: int, filter: str | None = None) -> dict:
        s = self._session(pid)
        try:
            modules = s.adapter.modules()
        except MemoryEngineError as exc:
            raise MemoryError_(str(exc)) from exc
        modules = [m for m in modules if not filter or filter.lower() in m["path"].lower()]
        return {"pid": pid, "count": len(modules), "modules": modules}

    @staticmethod
    def _read_adapter(adapter, address, size):
        try:
            return adapter.read_bytes(address, size)
        except PartialReadError:
            return None

    @staticmethod
    def _pack(vtype, value):
        return struct.pack(TYPES[vtype][0], float(value) if vtype in ("float", "double") else int(value))

    @staticmethod
    def _unpack(vtype, raw):
        return struct.unpack(TYPES[vtype][0], raw[:TYPES[vtype][1]])[0]

    # ---------- scanning ----------
    @_serialized
    def scan(self, pid: int, vtype: str = "int", value=None, between=None, unknown: bool = False) -> dict:
        """First scan. exact `value`, `between`=[lo,hi], or `unknown` snapshot."""
        s = self._session(pid)
        if value is None and between is None and not unknown:
            raise MemoryError_("Provide an exact value, between=[lo,hi], or unknown=true.")
        if vtype not in TYPES:
            raise MemoryError_(f"Unsupported value type {vtype!r}; use one of {', '.join(TYPES)}.")
        s.vtype = vtype
        fmt, size = TYPES[vtype]
        s.results, s.snapshot = [], {}
        scanned = 0
        needle = self._pack(vtype, value) if value is not None else None
        lo, hi = (between or [None, None])
        for start, length in s.adapter.regions(writable_only=True):
            off = 0
            while off < length:
                chunk = self._read_adapter(s.adapter, start + off, min(1024 * 1024, length - off))
                if chunk is None:
                    off += 1024 * 1024
                    continue
                scanned += len(chunk)
                for i in range(0, len(chunk) - size + 1, size):
                    raw = chunk[i:i + size]
                    if needle is not None:
                        if raw != needle:
                            continue
                    elif between is not None:
                        v = struct.unpack(fmt, raw)[0]
                        if not (lo <= v <= hi):
                            continue
                    # else unknown: keep everything (snapshot)
                    addr = start + off + i
                    s.results.append(addr)
                    if unknown or between is not None:
                        s.snapshot[addr] = raw
                    if len(s.results) >= MAX_RESULTS:
                        break
                off += len(chunk)
                if len(s.results) >= MAX_RESULTS or scanned >= MAX_SCAN_BYTES:
                    break
            if len(s.results) >= MAX_RESULTS or scanned >= MAX_SCAN_BYTES:
                break
        if needle is not None and not unknown and between is None:
            for addr in s.results:
                raw = self._read_adapter(s.adapter, addr, size)
                if raw:
                    s.snapshot[addr] = raw
        return {"pid": pid, "type": vtype, "matches": len(s.results),
                "capped": len(s.results) >= MAX_RESULTS,
                "sample": [hex(a) for a in s.results[:20]]}

    @_serialized
    def next_scan(self, pid: int, comparison: str = "exact", value=None) -> dict:
        """Narrow previous results. comparison: exact|changed|unchanged|increased|
        decreased|greater|less|between (value=[lo,hi])."""
        s = self._session(pid)
        fmt, size = TYPES[s.vtype]
        keep, newsnap = [], {}
        for addr in s.results:
            raw = self._read_adapter(s.adapter, addr, size)
            if raw is None:
                continue
            cur = struct.unpack(fmt, raw)[0]
            old_raw = s.snapshot.get(addr)
            old = struct.unpack(fmt, old_raw)[0] if old_raw else None
            ok = False
            if comparison == "exact":
                ok = cur == (float(value) if s.vtype in ("float", "double") else int(value))
            elif comparison == "changed":
                ok = old is not None and cur != old
            elif comparison == "unchanged":
                ok = old is not None and cur == old
            elif comparison == "increased":
                ok = old is not None and cur > old
            elif comparison == "decreased":
                ok = old is not None and cur < old
            elif comparison == "greater":
                ok = cur > value
            elif comparison == "less":
                ok = cur < value
            elif comparison == "between":
                ok = value[0] <= cur <= value[1]
            if ok:
                keep.append(addr)
                newsnap[addr] = raw
        s.results, s.snapshot = keep, newsnap
        return {"pid": pid, "type": s.vtype, "comparison": comparison, "matches": len(keep),
                "sample": [{"address": hex(a), "value": self._unpack(s.vtype, s.snapshot[a])} for a in keep[:20]]}

    @_serialized
    def results(self, pid: int, limit: int = 100) -> dict:
        s = self._session(pid)
        _, size = TYPES[s.vtype]
        rows = []
        for addr in s.results[:max(1, min(int(limit), 1000))]:
            raw = self._read_adapter(s.adapter, addr, size)
            rows.append({"address": hex(addr), "value": self._unpack(s.vtype, raw) if raw else None})
        return {"pid": pid, "type": s.vtype, "matches": len(s.results), "results": rows}

    # ------------- shutdown -------------
    @_serialized
    def shutdown(self):
        for session in list(self._sessions.values()):
            self._retire_session(session)
        self._sessions.clear()
