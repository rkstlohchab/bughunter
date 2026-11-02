#!/usr/bin/env python3
"""BugHunter native process-memory MCP server (stdio, JSON-RPC 2.0, stdlib only).

macOS counterpart to the Cheat Engine MCP bridge. Talks to the target through Mach VM calls
(task_for_pid / mach_vm_read_overwrite / mach_vm_write / mach_vm_region) via ctypes.

Design rules (see AGENTS.md invariant 7):
  * Structured tools only. There is deliberately NO eval / exec / shell tool here.
  * stdout carries JSON-RPC only; diagnostics go to stderr.
  * Approval gating happens in the desktop app (ce_policy.rs) before any call reaches this file.

macOS reality check: task_for_pid on another process needs root or a debugger-entitled caller, and is
refused for SIP-protected and hardened-runtime targets. check_access reports what this process can do.

A /proc backend exists so the protocol and scan logic are testable on Linux. It is a dev/test aid only.
"""
import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time

K_MAX_READ = 65536
K_MAX_WRITE = 4096
K_CHUNK = 4 * 1024 * 1024
K_MAX_STORED = 200_000
K_FREEZE_INTERVAL_S = 0.05
K_DEFAULT_SCAN_TIMEOUT_S = 60

TYPES = {
    "i8": ("b", 1), "u8": ("B", 1), "i16": ("h", 2), "u16": ("H", 2),
    "i32": ("i", 4), "u32": ("I", 4), "i64": ("q", 8), "u64": ("Q", 8),
    "f32": ("f", 4), "f64": ("d", 8),
}
SCAN_TYPES = sorted(TYPES) + ["string", "bytes"]
KERN = {1: "KERN_INVALID_ADDRESS", 2: "KERN_PROTECTION_FAILURE", 5: "KERN_FAILURE", 4: "KERN_INVALID_ARGUMENT"}


class MemError(Exception):
    pass


# ------------------------------------------------------------------ helpers

def parse_int(x):
    if isinstance(x, bool):
        raise ValueError("expected a number")
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str):
        return int(x.strip(), 0)
    raise ValueError(f"expected an integer, got {x!r}")


def parse_hex(s, allow_wildcards=False):
    toks = str(s).replace(",", " ").split()
    if len(toks) == 1 and len(toks[0]) > 2 and len(toks[0]) % 2 == 0 and "?" not in toks[0]:
        t = toks[0]
        toks = [t[i:i + 2] for i in range(0, len(t), 2)]
    out = []
    for t in toks:
        t = t[2:] if t.lower().startswith("0x") else t
        if t in ("?", "??"):
            if not allow_wildcards:
                raise ValueError("wildcards are only allowed in scans")
            out.append(None)
        else:
            out.append(int(t, 16))
            if not 0 <= out[-1] <= 255:
                raise ValueError(f"bad byte {t!r}")
    if not out:
        raise ValueError("empty byte pattern")
    return out


def encode(vtype, value):
    if vtype in TYPES:
        fmt, _ = TYPES[vtype]
        v = float(value) if fmt in "fd" else parse_int(value)
        try:
            return struct.pack("<" + fmt, v)
        except (struct.error, OverflowError):
            raise ValueError(f"value {value!r} does not fit {vtype}")
    if vtype == "string":
        return str(value).encode("utf-8")
    if vtype == "bytes":
        return bytes(parse_hex(value))
    raise ValueError(f"unknown type {vtype!r}; use one of {', '.join(SCAN_TYPES)}")


def decode(vtype, data):
    if vtype in TYPES:
        fmt, size = TYPES[vtype]
        return struct.unpack("<" + fmt, data[:size])[0]
    if vtype == "string":
        return data.decode("utf-8", "replace")
    return data.hex(" ")


def hexdump(base, data):
    lines = []
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"0x{base + i:016x}  {row.hex(' '):<47}  {asc}")
    return "\n".join(lines)


# ----------------------------------------------------------------- backends

class MachBackend:
    """macOS. Needs root or a debugger-entitled caller; refused for SIP / hardened targets."""
    name = "mach"
    PROT_R, PROT_W, PROT_X = 1, 2, 4

    class _RegionInfo(ctypes.Structure):
        _pack_ = 4  # vm_region_basic_info_data_64_t is packed to 4 bytes (9 natural_t words)
        _fields_ = [("protection", ctypes.c_int), ("max_protection", ctypes.c_int),
                    ("inheritance", ctypes.c_uint32), ("shared", ctypes.c_uint32),
                    ("reserved", ctypes.c_uint32), ("offset", ctypes.c_uint64),
                    ("behavior", ctypes.c_int), ("user_wired_count", ctypes.c_uint16)]

    def __init__(self):
        c = ctypes
        lib = c.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.task_for_pid.argtypes = [c.c_uint32, c.c_int, c.POINTER(c.c_uint32)]
        lib.task_for_pid.restype = c.c_int
        lib.mach_vm_read_overwrite.argtypes = [c.c_uint32, c.c_uint64, c.c_uint64, c.c_uint64, c.POINTER(c.c_uint64)]
        lib.mach_vm_read_overwrite.restype = c.c_int
        lib.mach_vm_write.argtypes = [c.c_uint32, c.c_uint64, c.c_uint64, c.c_uint32]
        lib.mach_vm_write.restype = c.c_int
        lib.mach_vm_region.argtypes = [c.c_uint32, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64), c.c_int,
                                       c.c_void_p, c.POINTER(c.c_uint32), c.POINTER(c.c_uint32)]
        lib.mach_vm_region.restype = c.c_int
        lib.mach_port_deallocate.argtypes = [c.c_uint32, c.c_uint32]
        lib.mach_port_deallocate.restype = c.c_int
        self.lib = lib
        self.self_task = c.c_uint32.in_dll(lib, "mach_task_self_").value
        self.task = None
        self._owns_task_ref = False

    @staticmethod
    def _kr(kr):
        return f"{KERN.get(kr, 'kern_return')}({kr})"

    def open(self, pid):
        if pid == os.getpid():
            # Self-testing uses our own task port, not task_for_pid: requesting
            # a debugger task port from the kernel can fail for a non-entitled
            # app even when reading/writing its OWN memory is permitted.
            self.task = self.self_task
            self._owns_task_ref = False
            return
        task = ctypes.c_uint32(0)
        kr = self.lib.task_for_pid(self.self_task, pid, ctypes.byref(task))
        if kr != 0:
            raise MemError(
                f"task_for_pid({pid}) failed: {self._kr(kr)}. The target may be SIP/hardened "
                "or the helper may lack debugger authorization. Do not disable SIP; "
                "use only authorized, unprotected processes.")
        self.task = task.value
        self._owns_task_ref = True

    def close(self):
        if self.task is not None:
            if self._owns_task_ref:
                self.lib.mach_port_deallocate(self.self_task, self.task)
            self.task = None
            self._owns_task_ref = False

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        out = ctypes.c_uint64(0)
        kr = self.lib.mach_vm_read_overwrite(self.task, addr, size, ctypes.addressof(buf), ctypes.byref(out))
        if kr != 0:
            raise MemError(f"read of {size} bytes at 0x{addr:x} failed: {self._kr(kr)}")
        return buf.raw[:out.value]

    def write(self, addr, data):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        kr = self.lib.mach_vm_write(self.task, addr, ctypes.addressof(buf), len(data))
        if kr != 0:
            raise MemError(f"write of {len(data)} bytes at 0x{addr:x} failed: {self._kr(kr)} "
                           "(page may be read-only or the target is protected)")

    def regions(self):
        addr = ctypes.c_uint64(0)
        while True:
            size = ctypes.c_uint64(0)
            info = self._RegionInfo()
            count = ctypes.c_uint32(9)  # VM_REGION_BASIC_INFO_COUNT_64
            obj = ctypes.c_uint32(0)
            kr = self.lib.mach_vm_region(self.task, ctypes.byref(addr), ctypes.byref(size), 9,
                                         ctypes.byref(info), ctypes.byref(count), ctypes.byref(obj))
            if kr != 0 or size.value == 0:
                return
            yield addr.value, size.value, info.protection & 7
            addr.value += size.value

    def access_report(self):
        euid = os.geteuid()
        return {
            "backend": "mach", "euid": euid, "is_root": euid == 0,
            "own_task_port_available": bool(self.self_task),
            "other_process_access": "unverified",
            "note": "The helper can test its own memory without task_for_pid. "
                    "Other processes require separate debugger authorization and may be protected. "
                    "Do not disable macOS security protections."
        }


class ProcfsBackend:
    """Linux /proc backend. Dev/test aid so the server logic can be exercised off-device."""
    name = "procfs"

    def __init__(self):
        self.fd = None
        self.pid = None

    def open(self, pid):
        try:
            self.fd = os.open(f"/proc/{pid}/mem", os.O_RDWR)
        except OSError as e:
            raise MemError(f"cannot open /proc/{pid}/mem: {e}")
        self.pid = pid

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self, addr, size):
        try:
            return os.pread(self.fd, size, addr)
        except (OSError, OverflowError) as e:
            raise MemError(f"read of {size} bytes at 0x{addr:x} failed: {e}")

    def write(self, addr, data):
        try:
            os.pwrite(self.fd, bytes(data), addr)
        except (OSError, OverflowError) as e:
            raise MemError(f"write of {len(data)} bytes at 0x{addr:x} failed: {e}")

    def regions(self):
        with open(f"/proc/{self.pid}/maps") as f:
            for line in f:
                rng, perms = line.split()[:2]
                lo, hi = (int(x, 16) for x in rng.split("-"))
                prot = (1 if perms[0] == "r" else 0) | (2 if perms[1] == "w" else 0) | (4 if perms[2] == "x" else 0)
                yield lo, hi - lo, prot

    def access_report(self):
        return {"backend": "procfs (dev/test only)", "euid": os.geteuid()}


def make_backend():
    if sys.platform == "darwin":
        return MachBackend()
    if sys.platform.startswith("linux"):
        return ProcfsBackend()
    raise MemError(f"unsupported platform {sys.platform}")


# ------------------------------------------------------------------ session

class Session:
    def __init__(self):
        self.backend = None
        self.pid = None
        self.lock = threading.RLock()
        self.scan = None  # dict(type, size, align, values={addr: bytes}, truncated)
        self.frozen = {}
        self.next_freeze_id = 1
        self.freezer = None
        self.stop_freeze = threading.Event()

    def need(self):
        if self.backend is None:
            raise MemError("no process attached; call attach_process first")
        return self.backend

    def attach(self, pid):
        pid = parse_int(pid)
        if pid <= 1:
            raise ValueError("refusing pid <= 1")
        if pid == os.getpid():
            raise ValueError("refusing to attach to the MCP server itself")
        with self.lock:
            self.detach()
            backend = make_backend()
            backend.open(pid)
            self.backend, self.pid = backend, pid

    def detach(self):
        with self.lock:
            self.stop_freezer()
            self.frozen.clear()
            self.scan = None
            if self.backend is not None:
                self.backend.close()
            self.backend, self.pid = None, None

    # --- freeze loop
    def ensure_freezer(self):
        if self.freezer and self.freezer.is_alive():
            return
        self.stop_freeze.clear()

        def loop():
            while not self.stop_freeze.wait(K_FREEZE_INTERVAL_S):
                with self.lock:
                    for addr, data in list(self.frozen.values()):
                        try:
                            self.backend.write(addr, data)
                        except Exception:
                            pass

        self.freezer = threading.Thread(target=loop, daemon=True)
        self.freezer.start()

    def stop_freezer(self):
        self.stop_freeze.set()

    # --- scanning
    def first_scan(self, vtype, value, max_stored, timeout_s):
        b = self.need()
        if vtype == "bytes":
            toks = parse_hex(value, allow_wildcards=True)
            pat_len = len(toks)
            regex = re.compile(b"".join(b"." if t is None else re.escape(bytes([t])) for t in toks), re.DOTALL)
            literal = None if any(t is None for t in toks) else bytes(toks)
        else:
            literal = encode(vtype, value)
            pat_len = len(literal)
            regex = None
        align = TYPES[vtype][1] if vtype in TYPES else 1
        overlap = pat_len - 1
        found, truncated, partial = {}, False, False
        deadline = time.monotonic() + timeout_s
        for start, size, prot in b.regions():
            if not prot & 1:
                continue
            for off in range(0, size, K_CHUNK):
                if time.monotonic() > deadline:
                    partial = True
                    break
                n = min(K_CHUNK + overlap, size - off)
                if n < pat_len:
                    continue
                try:
                    data = b.read(start + off, n)
                except MemError:
                    continue
                limit = min(K_CHUNK, len(data))
                for pos in self._matches(data, literal, regex, limit):
                    addr = start + off + pos
                    if addr % align:
                        continue
                    found[addr] = data[pos:pos + pat_len]
                    if len(found) >= max_stored:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated or partial:
                break
        with self.lock:
            self.scan = {"type": vtype, "size": pat_len, "values": found, "truncated": truncated}
        return len(found), truncated, partial

    @staticmethod
    def _matches(data, literal, regex, limit):
        if literal is not None:
            pos = data.find(literal)
            while pos != -1 and pos < limit:
                yield pos
                pos = data.find(literal, pos + 1)
        else:
            for m in regex.finditer(data):  # non-overlapping; fine for wildcard AOB
                if m.start() < limit:
                    yield m.start()

    def next_scan(self, mode, value):
        b = self.need()
        s = self.scan
        if not s:
            raise MemError("no active scan; run scan_value first")
        vtype, size = s["type"], s["size"]
        if mode in ("increased", "decreased") and vtype not in TYPES:
            raise ValueError(f"{mode} needs a numeric type")
        want = encode(vtype, value) if mode == "exact" else None
        if mode == "exact" and want is None:
            raise ValueError("exact needs a value")
        keep = {}
        for addr, old in s["values"].items():
            try:
                cur = b.read(addr, size)
            except MemError:
                continue
            if len(cur) != size:
                continue
            if mode == "exact":
                ok = cur == want
            elif mode == "changed":
                ok = cur != old
            elif mode == "unchanged":
                ok = cur == old
            elif mode == "increased":
                ok = decode(vtype, cur) > decode(vtype, old)
            elif mode == "decreased":
                ok = decode(vtype, cur) < decode(vtype, old)
            else:
                raise ValueError("mode must be exact, changed, unchanged, increased or decreased")
            if ok:
                keep[addr] = cur
        with self.lock:
            s["values"] = keep
        return len(keep)


S = Session()


# -------------------------------------------------------------------- tools

def t_ping(_a):
    return "pong"


def t_bridge_self_test(_a):
    """Check genuine read/write/readback on a temporary value OWNED BY THIS MCP PROCESS.

    Does not attach to a game, access another PID, or alter the current S session.
    Uses the same native Mach or Linux procfs read/write backend as live tools.
    """
    original = 0x1234AABBCCDD6719
    replacement = 0x5432BBAA11226719
    sample = ctypes.c_uint64(original)
    address = ctypes.addressof(sample)
    backend = make_backend()
    attached = False
    try:
        backend.open(os.getpid())
        attached = True
        observed = backend.read(address, 8)
        if observed != struct.pack('<Q', original):
            raise MemError('The backend read the wrong initial value in its own process.')
        backend.write(address, struct.pack('<Q', replacement))
        after = backend.read(address, 8)
        if after != struct.pack('<Q', replacement) or sample.value != replacement:
            raise MemError('The backend could not confirm the write in its own process.')
        # Restoration is part of a successful test, not optional cleanup.
        backend.write(address, struct.pack('<Q', original))
        restored = backend.read(address, 8)
        if restored != struct.pack('<Q', original) or sample.value != original:
            raise MemError('The temporary test value was not restored.')
        return json.dumps({
            'passed': True, 'backend': backend.name, 'tested_pid': os.getpid(),
            'steps': ['read owned value', 'write replacement', 'confirm readback', 'restore original'],
            'external_process_touched': False,
            'what_this_proves': 'Native memory read/write works inside this helper process.',
            'what_this_does_not_prove': 'Access to a game or to protected/other processes; Cheat Engine integration.'
        })
    finally:
        if attached:
            # Ensure we restore even if a later readback step fails.
            if sample.value != original:
                try:
                    backend.write(address, struct.pack('<Q', original))
                except Exception:
                    pass
        backend.close()


def t_check_access(_a):
    try:
        report = make_backend().access_report()
    except MemError as e:
        report = {"error": str(e)}
    report["attached_pid"] = S.pid
    return json.dumps(report, indent=2)


def t_list_processes(a):
    needle = str(a.get("filter", "")).lower()
    limit = min(parse_int(a.get("limit", 100)), 500)
    out = subprocess.run(["ps", "-axo", "pid=,user=,comm="], capture_output=True, text=True, timeout=10).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and needle in (parts[2] + " " + parts[1]).lower():
            rows.append(f"{parts[0]:>7}  {parts[1]:<12} {parts[2]}")
    return "\n".join(rows[:limit]) + (f"\n... {len(rows) - limit} more" if len(rows) > limit else "") or "no matches"


def t_attach_process(a):
    S.attach(a["pid"])
    return f"attached to pid {S.pid} via {S.backend.name}"


def t_detach_process(_a):
    pid = S.pid
    S.detach()
    return f"detached from pid {pid}" if pid else "nothing attached"


def t_get_process_info(_a):
    b = S.need()
    regs = list(b.regions())
    total = sum(r[1] for r in regs)
    return json.dumps({"pid": S.pid, "backend": b.name, "regions": len(regs), "mapped_bytes": total,
                       "frozen": len(S.frozen), "scan_results": len(S.scan["values"]) if S.scan else 0}, indent=2)


def t_list_memory_regions(a):
    b = S.need()
    limit = min(parse_int(a.get("limit", 200)), 2000)
    want_w = bool(a.get("writable_only", False))
    want_x = bool(a.get("executable_only", False))
    rows = []
    for start, size, prot in b.regions():
        if not prot & 1:
            continue
        if want_w and not prot & 2:
            continue
        if want_x and not prot & 4:
            continue
        rows.append(f"0x{start:016x}-0x{start + size:016x}  {size:>12}  {'r' if prot & 1 else '-'}{'w' if prot & 2 else '-'}{'x' if prot & 4 else '-'}")
    return "\n".join(rows[:limit]) + (f"\n... {len(rows) - limit} more" if len(rows) > limit else "") or "no readable regions"


def t_read_memory(a):
    b = S.need()
    addr = parse_int(a["address"])
    vtype = a.get("type")
    size = parse_int(a.get("size", TYPES[vtype][1] if vtype in TYPES else 64))
    if not 1 <= size <= K_MAX_READ:
        raise ValueError(f"size must be 1..{K_MAX_READ}")
    data = b.read(addr, size)
    head = f"{decode(vtype, data)}\n" if vtype and len(data) >= (TYPES[vtype][1] if vtype in TYPES else 1) else ""
    return head + hexdump(addr, data)


def t_read_pointer_chain(a):
    b = S.need()
    cur = parse_int(a["base"])
    offsets = [parse_int(o) for o in a.get("offsets", [])]
    if len(offsets) > 16:
        raise ValueError("at most 16 offsets")
    trail = [f"0x{cur:x}"]
    for off in offsets:
        ptr = struct.unpack("<Q", b.read(cur, 8))[0]
        cur = ptr + off
        trail.append(f"-> [0x{ptr:x}] + 0x{off:x} = 0x{cur:x}")
    vtype = a.get("type", "i32")
    size = TYPES[vtype][1] if vtype in TYPES else parse_int(a.get("size", 8))
    val = decode(vtype, b.read(cur, size))
    return "\n".join(trail) + f"\nfinal address 0x{cur:x} = {val} ({vtype})"



def find_pointer_references(backend, target, max_offset=1024, max_results=40,
                            max_bytes=32 * 1024 * 1024, timeout_s=5.0):
    """Find bounded, read-only 64-bit pointer *candidates* for a known address.

    A pointer address found in the heap is NOT stable across launches. Validity
    requires independently identifying a stable module-relative base and
    checking the chain in a new process instance.
    """
    if target <= 0 or max_offset < 0 or max_offset > 4096:
        raise ValueError('Invalid target or offset range')
    if not 1 <= max_results <= 64 or not 1024 <= max_bytes <= 32 * 1024 * 1024:
        raise ValueError('Invalid result or byte budget')
    deadline = time.monotonic() + min(max(0.1, timeout_s), 5.0)
    scanned, hits, partial = 0, [], False
    for start, size, prot in backend.regions():
        if not prot & 1 or size <= 0:
            continue
        for chunk_offset in range(0, size, 64 * 1024):
            if scanned >= max_bytes or time.monotonic() >= deadline:
                partial = True
                return hits, scanned, partial
            address = start + chunk_offset
            amount = min(size - chunk_offset, 64 * 1024, max_bytes - scanned)
            if amount < 8:
                break
            scanned += amount
            try:
                data = backend.read(address, amount)
            except MemError:
                continue
            aligned = (-address) % 8
            for position in range(aligned, len(data) - 7, 8):
                value = struct.unpack_from('<Q', data, position)[0]
                offset = target - value
                if 0 <= offset <= max_offset:
                    hits.append({'pointer_address': f'0x{address + position:x}',
                                 'pointee': f'0x{value:x}', 'offset': offset})
                    if len(hits) >= max_results:
                        return hits, scanned, True
    return hits, scanned, partial


def t_find_pointer_references(a):
    b = S.need()
    target = parse_int(a['target_address'])
    offset = parse_int(a.get('max_offset', 1024))
    limit = min(max(parse_int(a.get('limit', 30)), 1), 64)
    hits, scanned, partial = find_pointer_references(b, target, offset, limit)
    return json.dumps({'target': f'0x{target:x}', 'candidates': hits,
                       'scanned_bytes': scanned, 'partial': partial,
                       'verified_stable': False,
                       'note': 'Unverified pointer references only. Reattach after restart, resolve a module-relative base, and read back the value before marking any chain stable.'}, indent=2)


def process_instance_identity(pid):
    """Best-effort process creation identity; unavailable means no restart claim."""
    if sys.platform.startswith('linux'):
        try:
            stat = open(f'/proc/{pid}/stat', encoding='utf-8').read()
            # comm may contain spaces/parentheses; use the last closing paren.
            fields = stat[stat.rfind(')') + 2:].split()
            return f'{pid}:linux-ticks:{int(fields[19])}'  # /proc stat field 22
        except (OSError, IndexError, ValueError):
            return None
    if sys.platform == 'darwin':
        try:
            done = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                                  text=True, timeout=2, check=False)
            stamp = done.stdout.strip()
            if done.returncode == 0 and stamp:
                return f'{pid}:mac:{stamp}'
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def t_verify_pointer_chain(a):
    """Read-only check of an operator-supplied pointer chain in this process."""
    b = S.need()
    current = parse_int(a['base'])
    offsets = [parse_int(o) for o in a.get('offsets', [])]
    if len(offsets) > 12:
        raise ValueError('At most 12 pointer hops')
    vtype = a.get('type', 'i32')
    if vtype not in TYPES:
        raise ValueError('Verification requires a numeric type')
    trail = []
    for offset in offsets:
        raw = b.read(current, 8)
        if len(raw) != 8:
            raise MemError('Short pointer read')
        pointee = struct.unpack('<Q', raw)[0]
        current = pointee + offset
        trail.append(f'0x{current:x}')
    size = TYPES[vtype][1]
    raw = b.read(current, size)
    if len(raw) != size:
        raise MemError('Short final-value read')
    value = decode(vtype, raw)
    expected = decode(vtype, encode(vtype, a['expected_value']))
    return json.dumps({'attached_pid': S.pid, 'process_instance_id': process_instance_identity(S.pid),
                       'resolved_address': f'0x{current:x}',
                       'value': value, 'expected': expected, 'matched': value == expected,
                       'pointer_hops': trail, 'stable_across_restarts': 'not_established',
                       'note': 'This validates only the currently attached process. Repeat in an independently launched process and review module-relative bases.'}, indent=2)

def resolve_chain(backend, base, offsets):
    """Resolve a pointer recipe afresh on every call; never cache heap addresses."""
    current = parse_int(base)
    if current <= 0:
        raise ValueError('Pointer base must be positive.')
    if not isinstance(offsets, list) or len(offsets) > 12:
        raise ValueError('Use at most 12 pointer hops.')
    trail = []
    for raw_offset in offsets:
        offset = parse_int(raw_offset)
        if not 0 <= offset <= 4096:
            raise ValueError('Pointer offsets must be between 0 and 4096.')
        raw = backend.read(current, 8)
        if len(raw) != 8:
            raise MemError('Short pointer read.')
        pointer = struct.unpack('<Q', raw)[0]
        if not pointer:
            raise MemError('Null pointer; target may have changed or exited.')
        current = pointer + offset
        if current > 0xffffffffffffffff:
            raise MemError('Pointer address overflow.')
        trail.append({'pointer': hex(pointer), 'resolved': hex(current), 'offset': offset})
    return current, trail


def t_watch_value(a):
    """Finite, read-only real-time sampling. Re-resolve pointers on each sample."""
    backend = S.need()
    vtype = a.get('type', 'i32')
    if vtype not in TYPES:
        raise ValueError('Numeric type required.')
    count = parse_int(a.get('samples', 10))
    interval = float(a.get('interval_s', 0.2))
    if not 1 <= count <= 40 or not 0.1 <= interval <= 2.0 or (count - 1) * interval > 15:
        raise ValueError('Limit to 40 samples, 0.1..2 seconds apart, 15 seconds total.')
    if not isinstance(a.get('offsets', []), list):
        raise ValueError('Offsets must be a list.')
    base = parse_int(a['base'])
    offsets = a.get('offsets', [])
    initial_pid = S.pid
    identity = process_instance_identity(initial_pid)
    samples = []
    for i in range(count):
        if S.pid != initial_pid:
            raise MemError('Process attachment changed during observation.')
        if i:
            time.sleep(interval)
        with S.lock:
            address, trail = resolve_chain(backend, base, offsets)
            raw = backend.read(address, TYPES[vtype][1])
        if len(raw) != TYPES[vtype][1]:
            raise MemError('Short value read.')
        samples.append({'elapsed_s': round(i * interval, 3), 'address': hex(address),
                        'value': decode(vtype, raw)})
    return json.dumps({'pid': initial_pid, 'process_instance_id': identity,
                       'samples': samples, 'pointer_trail': trail,
                       'read_only': True, 'restart_stability': 'not_established'}, indent=2)


_ENTITY_FIELDS = {'x', 'y', 'z', 'health', 'team', 'visible', 'id'}

def t_read_entity_layout(a):
    """Read operator-provided structure layout; never guess player/entity offsets."""
    b = S.need()
    base = parse_int(a['base'])
    count = parse_int(a.get('count', 1))
    stride = parse_int(a.get('stride', 16))
    fields = a.get('fields')
    if not 1 <= count <= 32 or not 4 <= stride <= 512 or count * stride > K_MAX_READ:
        raise ValueError('Entity count/stride exceeds the read budget.')
    if not isinstance(fields, list) or not 1 <= len(fields) <= 7:
        raise ValueError('Provide between one and seven fields.')
    validated = []
    names = set()
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError('Invalid field specification.')
        name, kind = field.get('name'), field.get('type')
        offset = parse_int(field.get('offset', -1))
        if name not in _ENTITY_FIELDS or name in names or kind not in TYPES:
            raise ValueError('Unsupported or duplicate entity field.')
        if offset < 0 or offset + TYPES[kind][1] > stride:
            raise ValueError('Field is outside entity stride.')
        names.add(name)
        validated.append((name, kind, offset))
    rows = []
    # Individual bounded reads avoid exposing unrequested strings or credentials.
    with S.lock:
        for index in range(count):
            item = {'index': index}
            for name, kind, offset in validated:
                raw = b.read(base + index * stride + offset, TYPES[kind][1])
                if len(raw) != TYPES[kind][1]:
                    raise MemError('Short entity field read.')
                value = decode(kind, raw)
                if isinstance(value, float) and not (-1e6 < value < 1e6):
                    raise MemError('Unexpected coordinate value; check layout.')
                item[name] = value
            rows.append(item)
    return json.dumps({'attached_pid': S.pid, 'read_only': True, 'entities': rows,
                       'source': 'operator_supplied_layout',
                       'note': 'Fields were decoded from the supplied layout, not independently discovered.'}, indent=2)


def _modules_from_linux_maps(content):
    """Derive image slide from each mapped file offset; no guessed addresses."""
    found = {}
    for line in content.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        address, _protection, offset, _device, _inode, path = parts
        if not path.startswith('/'):
            continue
        try:
            start = int(address.split('-')[0], 16)
            slide = start - int(offset, 16)
        except (ValueError, IndexError):
            continue
        if slide < 0:
            continue
        path = path.removesuffix(' (deleted)')
        found[path] = min(slide, found.get(path, slide))
    return [{'name': os.path.basename(path), 'base': addr, 'source': 'proc_maps'}
            for path, addr in sorted(found.items())]


def _modules_from_vmmap(content):
    """Best-effort parser for `vmmap` shared-region lines with file-backed paths.

    This is NOT guaranteed to work on every macOS build. An empty parse is an
    explicit error, never an invitation to invent a module base.
    """
    found = {}
    for line in content.splitlines():
        match = re.match(r'^\s*\S+\s+([0-9A-Fa-f]{8,16})-([0-9A-Fa-f]{8,16})\s+.*\s(/.+?)\s*$', line)
        if not match:
            continue
        path = match.group(3).strip()
        if not path or path.endswith(']'):
            continue
        start = int(match.group(1), 16)
        found[path] = min(start, found.get(path, start))
    return [{'name': os.path.basename(path), 'base': addr, 'source': 'vmmap_unverified_parser'}
            for path, addr in sorted(found.items())]


def get_loaded_modules(pid):
    if not isinstance(pid, int) or pid < 2:
        raise ValueError('Attach to a process first.')
    if sys.platform.startswith('linux'):
        try:
            with open(f'/proc/{pid}/maps', encoding='utf-8', errors='replace') as stream:
                modules = _modules_from_linux_maps(stream.read())
        except OSError as exc:
            raise MemError(f'Module list unavailable: {exc}') from exc
    elif sys.platform == 'darwin':
        result = subprocess.run(['vmmap', '-w', str(pid)], capture_output=True,
                                text=True, timeout=8, check=False)
        if result.returncode:
            raise MemError('vmmap cannot inspect this process. Check permissions; do not disable macOS protections.')
        modules = _modules_from_vmmap(result.stdout)
    else:
        raise MemError('Module resolution is only implemented for macOS and Linux.')
    if not modules:
        raise MemError('No file-backed module mappings could be verified.')
    return modules


def t_list_loaded_modules(a):
    S.need()
    limit = parse_int(a.get('limit', 50))
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be between 1 and 100.')
    modules = get_loaded_modules(S.pid)
    return json.dumps({'pid': S.pid, 'module_count': len(modules),
                       'modules': [{**m, 'base': hex(m['base'])} for m in modules[:limit]],
                       'read_only': True,
                       'note': 'Module bases are runtime observations, not fixed pointer addresses.'}, indent=2)


def t_verify_module_pointer_chain(a):
    """Resolve an independently supplied module+RVA and validate current value."""
    backend = S.need()
    name = a.get('module_name')
    rva = parse_int(a.get('rva', -1))
    if not isinstance(name, str) or not 2 <= len(name) <= 160 or '/' in name or '\\' in name:
        raise ValueError('Supply a basename from list_loaded_modules.')
    if not 0 <= rva <= 256 * 1024 * 1024:
        raise ValueError('Module-relative offset is outside the allowed range.')
    vtype = a.get('type', 'i32')
    if vtype not in TYPES:
        raise ValueError('Use a numeric type.')
    matches = [m for m in get_loaded_modules(S.pid) if m['name'] == name]
    if len(matches) != 1:
        raise MemError('Module is missing or ambiguous. Do not guess a base address.')
    pointer_slot = matches[0]['base'] + rva
    with S.lock:
        value_address, trail = resolve_chain(backend, pointer_slot, a.get('offsets', []))
        raw = backend.read(value_address, TYPES[vtype][1])
    if len(raw) != TYPES[vtype][1]:
        raise MemError('Short read while verifying module pointer.')
    value = decode(vtype, raw)
    expected = decode(vtype, encode(vtype, a['expected_value']))
    return json.dumps({'pid': S.pid, 'process_instance_id': process_instance_identity(S.pid),
                       'module_name': name, 'module_base': hex(matches[0]['base']), 'rva': hex(rva),
                       'resolved_address': hex(value_address), 'pointer_trail': trail,
                       'value': value, 'expected': expected, 'matched': value == expected,
                       'read_only': True, 'stable_across_restarts': 'not_established',
                       'note': 'This is a single-process check. Repeat on a new instance with the same verified module/RVA recipe.'}, indent=2)


def t_write_memory(a):
    b = S.need()
    addr = parse_int(a["address"])
    vtype = a.get("type", "bytes")
    data = encode(vtype, a["value"])
    if not 1 <= len(data) <= K_MAX_WRITE:
        raise ValueError(f"write size must be 1..{K_MAX_WRITE} bytes")
    with S.lock:
        b.write(addr, data)
        back = b.read(addr, len(data))
    return f"wrote {len(data)} bytes at 0x{addr:x}; readback {'matches' if back == data else 'DIFFERS: ' + back.hex(' ')}"


def t_change_unique_scan_result(a):
    """Change a single, freshly verified scan result with readback.

    Never writes to an arbitrary model-proposed address. The native approval gate
    must approve the exact call; protected/remote processes may still reject it.
    """
    b = S.need()
    s = S.scan
    if not s or s.get("truncated"):
        raise MemError("A complete scan is required before a verified change.")
    if len(s["values"]) != 1:
        raise MemError("Narrow the scan to exactly one candidate before changing a value.")
    vtype = s["type"]
    if vtype not in TYPES:
        raise MemError("Only typed numeric scan results may be changed by this operation.")
    address, original = next(iter(s["values"].items()))
    expected = encode(vtype, a["expected_value"])
    replacement = encode(vtype, a["new_value"])
    if len(replacement) != s["size"] or len(replacement) > K_MAX_WRITE:
        raise MemError("Incorrect replacement size.")
    with S.lock:
        live = b.read(address, s["size"])
        if live != expected or live != original:
            raise MemError("The game's current value changed since the last scan. Run next_scan again.")
        b.write(address, replacement)
        readback = b.read(address, len(replacement))
        if readback != replacement:
            raise MemError("Write was sent, but the new value could not be verified.")
        s["values"] = {address: readback}
    return json.dumps({"attached_pid": S.pid, "address": hex(address), "type": vtype,
                       "old_value": decode(vtype, original), "new_value": decode(vtype, readback),
                       "readback_matches": True,
                       "note": "Verified one live value only. It may reset when the game changes or restarts."})


def t_scan_value(a):
    vtype = a["type"]
    if vtype not in SCAN_TYPES:
        raise ValueError(f"type must be one of {', '.join(SCAN_TYPES)}")
    max_stored = min(parse_int(a.get("max_results", K_MAX_STORED)), K_MAX_STORED)
    timeout_s = min(float(a.get("timeout_s", K_DEFAULT_SCAN_TIMEOUT_S)), 300)
    n, truncated, partial = S.first_scan(vtype, a["value"], max_stored, timeout_s)
    note = (" (result cap hit)" if truncated else "") + (" (timed out, partial)" if partial else "")
    return f"{n} matches{note}. Use get_scan_results, then next_scan to narrow."


def t_next_scan(a):
    n = S.next_scan(a["mode"], a.get("value"))
    return f"{n} results remain"


def t_get_scan_results(a):
    b = S.need()
    s = S.scan
    if not s:
        raise MemError("no active scan")
    limit = min(parse_int(a.get("limit", 50)), 500)
    offset = parse_int(a.get("offset", 0))
    keys = sorted(s["values"])[offset:offset + limit]
    rows = []
    for addr in keys:
        try:
            cur = decode(s["type"], b.read(addr, s["size"]))
        except MemError:
            cur = "<unreadable>"
        rows.append(f"0x{addr:016x}  {cur}")
    return f"{len(s['values'])} total, showing {len(keys)} from {offset}\n" + "\n".join(rows)


def t_clear_scan(_a):
    S.scan = None
    return "scan cleared"


def t_freeze_value(a):
    b = S.need()
    addr = parse_int(a["address"])
    data = encode(a.get("type", "i32"), a["value"])
    if len(data) > K_MAX_WRITE:
        raise ValueError("value too large")
    with S.lock:
        b.write(addr, data)
        fid = S.next_freeze_id
        S.next_freeze_id += 1
        S.frozen[fid] = (addr, data)
        S.ensure_freezer()
    return f"freeze {fid}: 0x{addr:x} held at {data.hex(' ')} every {int(K_FREEZE_INTERVAL_S * 1000)} ms"


def t_unfreeze_value(a):
    fid = parse_int(a["id"])
    with S.lock:
        if S.frozen.pop(fid, None) is None:
            raise KeyError(f"no freeze with id {fid}")
    return f"freeze {fid} released"


def t_list_frozen(_a):
    with S.lock:
        rows = [f"{fid}: 0x{addr:x} = {data.hex(' ')}" for fid, (addr, data) in S.frozen.items()]
    return "\n".join(rows) or "nothing frozen"


def schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


_T = {"type": "string", "enum": SCAN_TYPES, "description": "value type"}
_ADDR = {"type": ["string", "integer"], "description": "address, e.g. 0x1000 or decimal"}
TOOLS = [
    ("ping", "Check the bridge is alive.", schema({}), t_ping),
    ("check_access", "Report privilege state (root, task_for_pid) and the attached pid.", schema({}), t_check_access),
    ("bridge_self_test", "Safely prove native memory read/write using only a temporary value in the helper itself; no games touched.", schema({}), t_bridge_self_test),
    ("list_processes", "List running processes (pid, user, command); optional substring filter.",
     schema({"filter": {"type": "string"}, "limit": {"type": "integer"}}), t_list_processes),
    ("attach_process", "Attach to a process by pid (acquires its Mach task port). Detaches any previous target.",
     schema({"pid": {"type": "integer"}}, ["pid"]), t_attach_process),
    ("detach_process", "Release the current target, freezes and scan.", schema({}), t_detach_process),
    ("get_process_info", "Summary of the attached process: regions, mapped bytes, freezes, scan size.", schema({}), t_get_process_info),
    ("list_memory_regions", "Readable regions with protections.",
     schema({"writable_only": {"type": "boolean"}, "executable_only": {"type": "boolean"}, "limit": {"type": "integer"}}), t_list_memory_regions),
    ("read_memory", "Read memory and show a hexdump, plus a decoded value if type is given.",
     schema({"address": _ADDR, "size": {"type": "integer"}, "type": _T}, ["address"]), t_read_memory),
    ("read_pointer_chain", "Follow base -> [ptr]+off -> ... and read the final value.",
     schema({"base": _ADDR, "offsets": {"type": "array", "items": {"type": ["string", "integer"]}}, "type": _T}, ["base"]), t_read_pointer_chain),
    ("find_pointer_references", "Bounded read-only 64-bit pointer candidates near an observed address; NOT a stable-pointer claim.",
     schema({"target_address": _ADDR, "max_offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["target_address"]), t_find_pointer_references),
    ("verify_pointer_chain", "Read-only check of a supplied pointer chain and current expected value. Restart verification remains operator-reviewed.",
     schema({"base": _ADDR, "offsets": {"type": "array", "items": {"type": ["string", "integer"]}}, "type": _T,
             "expected_value": {"type": ["string", "number"]}}, ["base", "expected_value"]), t_verify_pointer_chain),
    ("watch_value", "Read-only bounded live health/money monitoring. Re-resolves pointer hops each sample; no restart-stability claim.",
     schema({"base": _ADDR, "offsets": {"type": "array", "items": _ADDR}, "type": _T,
             "samples": {"type": "integer"}, "interval_s": {"type": "number"}}, ["base"]), t_watch_value),
    ("read_entity_layout", "Read an explicitly supplied entity structure for an ESP-style visualization. The model must not invent offsets.",
     schema({"base": _ADDR, "count": {"type": "integer"}, "stride": {"type": "integer"},
             "fields": {"type": "array", "items": {"type": "object"}}}, ["base", "fields"]), t_read_entity_layout),
    ("list_loaded_modules", "List verified runtime bases of file-backed modules (macOS vmmap or Linux proc maps).",
     schema({"limit": {"type": "integer"}}), t_list_loaded_modules),
    ("verify_module_pointer_chain", "Read-only check of module+RVA pointer recipe, rebased at the attached process's current module base.",
     schema({"module_name": {"type": "string"}, "rva": _ADDR,
             "offsets": {"type": "array", "items": _ADDR}, "type": _T,
             "expected_value": {"type": ["string", "number"]}},
            ["module_name", "rva", "expected_value"]), t_verify_module_pointer_chain),
    ("write_memory", "Write a typed value or hex bytes at an address, then read it back.",
     schema({"address": _ADDR, "type": _T, "value": {"type": ["string", "number"]}}, ["address", "type", "value"]), t_write_memory),
    ("scan_value", "First scan for an exact value across readable regions. bytes type accepts '48 8B ?? 00' wildcards.",
     schema({"type": _T, "value": {"type": ["string", "number"]}, "max_results": {"type": "integer"}, "timeout_s": {"type": "number"}}, ["type", "value"]), t_scan_value),
    ("change_unique_scan_result", "Change exactly one confirmed numeric scan result after a live value check, then read back. Operator approval required.",
     schema({"expected_value": {"type": ["string", "number"]}, "new_value": {"type": ["string", "number"]}}, ["expected_value", "new_value"]), t_change_unique_scan_result),
    ("next_scan", "Narrow the active scan: exact (needs value), changed, unchanged, increased, decreased.",
     schema({"mode": {"type": "string", "enum": ["exact", "changed", "unchanged", "increased", "decreased"]}, "value": {"type": ["string", "number"]}}, ["mode"]), t_next_scan),
    ("get_scan_results", "List current scan results with live values.",
     schema({"limit": {"type": "integer"}, "offset": {"type": "integer"}}), t_get_scan_results),
    ("clear_scan", "Discard the active scan.", schema({}), t_clear_scan),
    ("freeze_value", "Write a value now and keep re-writing it every 50 ms until released.",
     schema({"address": _ADDR, "type": _T, "value": {"type": ["string", "number"]}}, ["address", "value"]), t_freeze_value),
    ("unfreeze_value", "Release a freeze by id.", schema({"id": {"type": "integer"}}, ["id"]), t_unfreeze_value),
    ("list_frozen", "List active freezes.", schema({}), t_list_frozen),
]
HANDLERS = {name: fn for name, _d, _s, fn in TOOLS}


# ----------------------------------------------------------------- protocol

def tool_list():
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s, _f in TOOLS]


def call_tool(name, args):
    fn = HANDLERS.get(name)
    if fn is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    try:
        text = fn(args or {})
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except KeyError as e:
        text = f"missing or unknown key: {e}"
    except (MemError, ValueError, struct.error, subprocess.SubprocessError) as e:
        text = str(e)
    except Exception as e:  # keep the server alive whatever the target does
        text = f"internal error: {e!r}"
    return {"content": [{"type": "text", "text": text}], "isError": True}


def handle(req):
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "bughunter-mac-memory", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        p = req.get("params") or {}
        result = call_tool(p.get("name"), p.get("arguments"))
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve():
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            resp = handle(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
    finally:
        S.detach()  # release the task port and stop the freezer on EOF


if __name__ == "__main__":
    serve()
