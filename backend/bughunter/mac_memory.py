"""Native macOS process-memory engine (the Cheat Engine / Bit Slicer workflow).

Local, single-user game/trainer tooling for the operator's own machine: list
processes, attach, scan + next-scan to isolate a value, read/write typed values,
freeze, enumerate modules, and resolve a pointer chain.

Reading/writing another task's memory on macOS goes through task_for_pid(), which
the kernel refuses unless the caller is privileged (root) or carries the
com.apple.security.cs.debugger entitlement AND the target is not a hardened
runtime. When it refuses, every call returns a precise diagnostic instead of a
silent failure.
"""
from __future__ import annotations

import ctypes
from functools import wraps
import struct
import threading
from ctypes import c_int, c_uint, c_ulonglong, c_void_p, c_size_t, POINTER, byref, create_string_buffer

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)

# --- mach constants ---
KERN_SUCCESS = 0
VM_PROT_READ, VM_PROT_WRITE = 0x1, 0x2
VM_REGION_BASIC_INFO_64 = 9
PROC_ALL_PIDS = 1

# struct vm_region_basic_info_64
class _BasicInfo64(ctypes.Structure):
    _fields_ = [("protection", c_int), ("max_protection", c_int),
                ("inheritance", c_uint), ("shared", c_int), ("reserved", c_int),
                ("offset", c_ulonglong), ("behavior", c_int), ("user_wired_count", ctypes.c_ushort)]
_BASIC_COUNT = ctypes.sizeof(_BasicInfo64) // ctypes.sizeof(c_int)

# --- function prototypes ---
_libc.task_for_pid.argtypes = [c_uint, c_int, POINTER(c_uint)]; _libc.task_for_pid.restype = c_int
_libc.mach_vm_read_overwrite.argtypes = [c_uint, c_ulonglong, c_ulonglong, c_ulonglong, POINTER(c_ulonglong)]
_libc.mach_vm_read_overwrite.restype = c_int
_libc.mach_vm_write.argtypes = [c_uint, c_ulonglong, c_ulonglong, c_uint]; _libc.mach_vm_write.restype = c_int
_libc.mach_port_deallocate.argtypes = [c_uint, c_uint]; _libc.mach_port_deallocate.restype = c_int
_libc.mach_vm_region.argtypes = [c_uint, POINTER(c_ulonglong), POINTER(c_ulonglong), c_int,
                                 c_void_p, POINTER(c_uint), POINTER(c_uint)]
_libc.mach_vm_region.restype = c_int
_libc.proc_listpids.argtypes = [c_uint, c_uint, c_void_p, c_int]; _libc.proc_listpids.restype = c_int
_libc.proc_name.argtypes = [c_int, c_void_p, c_uint]; _libc.proc_name.restype = c_int
_libc.proc_pidpath.argtypes = [c_int, c_void_p, c_uint]; _libc.proc_pidpath.restype = c_int

def _mach_task_self() -> int:
    return c_uint.in_dll(_libc, "mach_task_self_").value

# value type -> (struct format, size)
TYPES = {"int": ("<i", 4), "uint": ("<I", 4), "int16": ("<h", 2), "int64": ("<q", 8),
         "uint64": ("<Q", 8), "float": ("<f", 4), "double": ("<d", 8), "byte": ("<b", 1)}

MAX_RESULTS = 500_000
MAX_SCAN_BYTES = 3 * 1024**3


class MemoryError_(Exception):
    pass


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class _Session:
    def __init__(self, pid: int, task: int, name: str):
        self.pid, self.task, self.name = pid, task, name
        self.vtype = "int"
        self.results: list[int] = []          # candidate addresses
        self.snapshot: dict[int, bytes] = {}   # addr -> last-read raw value (for unknown/compare scans)
        self.freezes: dict[int, threading.Event] = {}


class MacMemory:
    """One engine per app; holds attached sessions keyed by pid."""

    def __init__(self):
        self.available = True
        self._self = _mach_task_self()
        self._sessions: dict[int, _Session] = {}
        self._lock = threading.RLock()

    # ---------- process discovery ----------
    def list_processes(self, filter: str | None = None) -> list[dict]:
        count = _libc.proc_listpids(PROC_ALL_PIDS, 0, None, 0)
        if count <= 0:
            raise MemoryError_("proc_listpids returned nothing.")
        n = count // 4
        buf = (c_int * (n + 64))()
        got = _libc.proc_listpids(PROC_ALL_PIDS, 0, buf, ctypes.sizeof(buf))
        pids = [buf[i] for i in range(got // 4) if buf[i] > 0]
        out = []
        namebuf = create_string_buffer(256)
        for pid in pids:
            namebuf.raw = b"\x00" * 256
            if _libc.proc_name(pid, namebuf, 256) > 0:
                name = namebuf.value.decode("utf-8", "replace")
            else:
                name = ""
            if filter and filter.lower() not in name.lower():
                continue
            out.append({"pid": pid, "name": name})
        out.sort(key=lambda p: p["name"].lower())
        return out

    # ---------- attach ----------
    def attach(self, pid: int) -> dict:
        with self._lock:
            task = c_uint(0)
            kr = _libc.task_for_pid(self._self, pid, byref(task))
            if kr != KERN_SUCCESS:
                raise MemoryError_(
                    f"task_for_pid(pid={pid}) failed (kern_return={kr}). macOS blocks cross-process "
                    f"memory unless the backend runs as root (sudo) or is signed with the debugger "
                    f"entitlement and the target is not a hardened-runtime build.")
            try:
                name = next((p["name"] for p in self.list_processes() if p["pid"] == pid), str(pid))
            except Exception:
                _libc.mach_port_deallocate(self._self, task.value)
                raise
            previous = self._sessions.pop(pid, None)
            if previous:
                self._retire_session(previous)
            self._sessions[pid] = _Session(pid, task.value, name)
            return {"pid": pid, "name": name, "attached": True}

    def _session(self, pid: int) -> _Session:
        s = self._sessions.get(pid)
        if not s:
            raise MemoryError_(f"Not attached to pid {pid}; call memory.attach first.")
        return s

    def detach(self, pid: int) -> dict:
        with self._lock:
            s = self._sessions.pop(pid, None)
            if s:
                self._retire_session(s)
            return {"pid": pid, "attached": False}

    def _retire_session(self, session: _Session):
        # Called with the engine lock: no operation or freezer can use the port
        # while its send right is released. Every task_for_pid success owns one.
        for stop in session.freezes.values():
            stop.set()
        session.freezes.clear()
        task, session.task = session.task, 0
        if task:
            _libc.mach_port_deallocate(self._self, task)

    # ---------- raw read/write ----------
    def _read(self, task: int, addr: int, size: int) -> bytes | None:
        buf = create_string_buffer(size)
        out = c_ulonglong(0)
        kr = _libc.mach_vm_read_overwrite(task, addr, size, ctypes.addressof(buf), byref(out))
        if kr != KERN_SUCCESS or out.value != size:
            return None
        return buf.raw[:size]

    def _write(self, task: int, addr: int, data: bytes) -> bool:
        buf = create_string_buffer(data, len(data))
        kr = _libc.mach_vm_write(task, addr, ctypes.addressof(buf), len(data))
        # A refused write must not alter the target's page permissions.
        return kr == KERN_SUCCESS

    # ---------- regions ----------
    def _regions(self, task: int, writable_only: bool = True):
        address = c_ulonglong(0)
        while True:
            size = c_ulonglong(0)
            info = _BasicInfo64()
            count = c_uint(_BASIC_COUNT)
            obj = c_uint(0)
            kr = _libc.mach_vm_region(task, byref(address), byref(size), VM_REGION_BASIC_INFO_64,
                                      ctypes.cast(byref(info), c_void_p), byref(count), byref(obj))
            if obj.value:
                _libc.mach_port_deallocate(self._self, obj.value)
            if kr != KERN_SUCCESS or size.value == 0:
                break
            prot = info.protection
            start, length = address.value, size.value
            readable = bool(prot & VM_PROT_READ)
            writable = bool(prot & VM_PROT_WRITE)
            if readable and (writable or not writable_only):
                yield start, length
            address = c_ulonglong(start + length)

    # ---------- typed helpers ----------
    @staticmethod
    def _pack(vtype: str, value) -> bytes:
        fmt, _ = TYPES[vtype]
        return struct.pack(fmt, int(value) if "float" not in vtype and vtype != "double" else float(value))

    @staticmethod
    def _unpack(vtype: str, raw: bytes):
        fmt, size = TYPES[vtype]
        if len(raw) < size:
            return None
        return struct.unpack(fmt, raw[:size])[0]

    @_serialized
    def read_value(self, pid: int, address: int, vtype: str = "int"):
        s = self._session(pid)
        _, size = TYPES[vtype]
        raw = self._read(s.task, address, size)
        if raw is None:
            raise MemoryError_(f"Could not read {size} bytes at {hex(address)}.")
        return {"pid": pid, "address": hex(address), "type": vtype, "value": self._unpack(vtype, raw)}

    @_serialized
    def write_value(self, pid: int, address: int, value, vtype: str = "int") -> dict:
        s = self._session(pid)
        ok = self._write(s.task, address, self._pack(vtype, value))
        if not ok:
            raise MemoryError_(f"Write to {hex(address)} failed (protected or unmapped). Page permissions were not changed.")
        return {"pid": pid, "address": hex(address), "type": vtype, "written": value}

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
        for start, length in self._regions(s.task, writable_only=True):
            off = 0
            while off < length:
                chunk = self._read(s.task, start + off, min(1024 * 1024, length - off))
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
                raw = self._read(s.task, addr, size)
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
            raw = self._read(s.task, addr, size)
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
            raw = self._read(s.task, addr, size)
            rows.append({"address": hex(addr), "value": self._unpack(s.vtype, raw) if raw else None})
        return {"pid": pid, "type": s.vtype, "matches": len(s.results), "results": rows}

    # ---------- freeze ----------
    @_serialized
    def freeze(self, pid: int, address: int, value, vtype: str = "int", enable: bool = True) -> dict:
        s = self._session(pid)
        prev = s.freezes.pop(address, None)
        if prev:
            prev.set()
        if not enable:
            return {"pid": pid, "address": hex(address), "frozen": False}
        data = self._pack(vtype, value)
        if not self._write(s.task, address, data):
            raise MemoryError_(f"Cannot freeze {hex(address)}: the initial write failed. Page permissions were not changed.")
        stop = threading.Event()
        s.freezes[address] = stop
        task = s.task

        def hold():
            while not stop.wait(0.05):
                with self._lock:
                    if stop.is_set() or self._sessions.get(pid) is not s:
                        break
                    if not self._write(task, address, data):
                        stop.set()
                        if s.freezes.get(address) is stop:
                            s.freezes.pop(address)
                        break
        threading.Thread(target=hold, daemon=True, name=f"freeze-{pid}-{address:x}").start()
        return {"pid": pid, "address": hex(address), "type": vtype, "value": value, "frozen": True}

    # ---------- modules / pointer chain ----------
    @_serialized
    def modules(self, pid: int, filter: str | None = None) -> dict:
        """Enumerate loaded images (dyld) with their base addresses."""
        s = self._session(pid)
        TASK_DYLD_INFO = 17
        class _DyldInfo(ctypes.Structure):
            _fields_ = [("all_image_info_addr", c_ulonglong), ("all_image_info_size", c_ulonglong),
                        ("all_image_info_format", c_int)]
        _libc.task_info.argtypes = [c_uint, c_uint, c_void_p, POINTER(c_uint)]
        _libc.task_info.restype = c_int
        info = _DyldInfo()
        count = c_uint(ctypes.sizeof(_DyldInfo) // ctypes.sizeof(c_int))
        kr = _libc.task_info(s.task, TASK_DYLD_INFO, ctypes.cast(byref(info), c_void_p), byref(count))
        if kr != KERN_SUCCESS:
            raise MemoryError_(f"task_info(DYLD) failed (kern_return={kr}).")
        hdr = self._read(s.task, info.all_image_info_addr, 40)
        if not hdr:
            raise MemoryError_("Could not read dyld_all_image_infos.")
        version, count32 = struct.unpack("<II", hdr[:8])
        info_array = struct.unpack("<Q", hdr[8:16])[0]
        mods = []
        for i in range(min(count32, 4096)):
            entry = self._read(s.task, info_array + i * 24, 24)  # {imageLoadAddress, imageFilePath, modDate}
            if not entry:
                break
            load_addr, path_ptr, _ = struct.unpack("<QQQ", entry)
            path = b""
            for j in range(0, 1024, 64):
                piece = self._read(s.task, path_ptr + j, 64)
                if not piece:
                    break
                path += piece
                if b"\x00" in piece:
                    break
            name = path.split(b"\x00")[0].decode("utf-8", "replace")
            if filter and filter.lower() not in name.lower():
                continue
            mods.append({"base": hex(load_addr), "path": name})
        return {"pid": pid, "count": len(mods), "modules": mods}

    @_serialized
    def resolve_chain(self, pid: int, base: int, offsets: list[int], vtype: str = "int") -> dict:
        """Daisy-chain: addr=base; for each offset but last addr=read_ptr(addr+off); final=addr+offsets[-1]."""
        s = self._session(pid)
        addr = int(base)
        trail = [hex(addr)]
        for off in offsets[:-1]:
            raw = self._read(s.task, addr + off, 8)
            if not raw:
                raise MemoryError_(f"Pointer read failed at {hex(addr + off)}.")
            addr = struct.unpack("<Q", raw)[0]
            trail.append(hex(addr))
        final = addr + (offsets[-1] if offsets else 0)
        raw = self._read(s.task, final, TYPES[vtype][1])
        return {"pid": pid, "final_address": hex(final), "trail": trail,
                "type": vtype, "value": self._unpack(vtype, raw) if raw else None}

    @_serialized
    def shutdown(self):
        for s in list(self._sessions.values()):
            self._retire_session(s)
        self._sessions.clear()
