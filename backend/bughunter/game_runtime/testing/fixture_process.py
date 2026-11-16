"""Child entrypoint for the disposable fixture target.

Invoke via ``python -m bughunter.game_runtime.testing.fixture_process
--layout DEFAULT``. The process allocates ``N`` anonymous memory regions
(see :data:`layout.DEFAULT_LAYOUT`) using the host OS's native allocator
(``mmap`` on POSIX, ``VirtualAlloc`` on Windows) so the oracle can read
those bytes directly through its own OS code path.

The process answers a line-delimited JSON RPC on **stdin/stdout**. stdout
is reserved for responses only; every log line goes to stderr. On
``shutdown`` the process exits cleanly.

RPC methods:

* ``identity`` → ``{pid, start_time_ns, module_fingerprint, region_map}``
* ``get`` → ``{value}`` (respects ``--control-lie`` for the named field)
* ``set`` → ``{ok: true}`` (always writes the real memory, even under a
  control-lie — the lie only affects ``get``)
* ``move`` → ``{new_region_map}`` (re-mmaps the named regions and
  rewrites their values so the layout is preserved at the new base)
* ``shutdown`` → ``{ok: true}``, then exits.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
import struct
import sys
import time
from typing import Any

from .layout import DEFAULT_LAYOUT, FieldSpec, Layout


# ---------------------------------------------------------------------------
# Native allocator glue
# ---------------------------------------------------------------------------


class _PosixAllocator:
    """Allocator backed by ``mmap(MAP_ANON | MAP_PRIVATE)``."""

    def __init__(self) -> None:
        libc_path = ctypes.util.find_library("c")
        if libc_path is None and sys.platform == "darwin":
            libc_path = "/usr/lib/libSystem.B.dylib"
        if libc_path is None:
            raise RuntimeError("libc not found")
        self.libc = ctypes.CDLL(libc_path, use_errno=True)

        self.libc.mmap.restype = ctypes.c_void_p
        self.libc.mmap.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_long,
        ]
        self.libc.munmap.restype = ctypes.c_int
        self.libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]

        self.PROT_READ = 0x1
        self.PROT_WRITE = 0x2
        self.MAP_PRIVATE = 0x0002
        if sys.platform == "darwin":
            self.MAP_ANON = 0x1000
        else:
            self.MAP_ANON = 0x0020  # Linux MAP_ANONYMOUS

        self._MAP_FAILED = ctypes.c_void_p(-1).value

    def alloc(self, size: int) -> int:
        addr = self.libc.mmap(
            None,
            size,
            self.PROT_READ | self.PROT_WRITE,
            self.MAP_PRIVATE | self.MAP_ANON,
            -1,
            0,
        )
        # ``mmap`` returns ``void *``; a successful call never equals
        # ``MAP_FAILED`` (all-ones). ``ctypes`` returns ``None`` for
        # zero, so coerce explicitly.
        addr_int = int(addr) if addr is not None else 0
        if addr_int == 0 or addr_int == self._MAP_FAILED:
            errno = ctypes.get_errno()
            raise OSError(errno, f"mmap failed: errno={errno}")
        # Zero the mapping explicitly — anonymous mappings on POSIX are
        # already zero but being explicit is cheap and keeps the oracle
        # tests honest.
        ctypes.memset(addr_int, 0, size)
        return addr_int

    def free(self, addr: int, size: int) -> None:
        ret = self.libc.munmap(ctypes.c_void_p(addr), size)
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"munmap failed: errno={errno}")


class _WindowsAllocator:
    """Allocator backed by ``VirtualAlloc`` / ``VirtualFree``."""

    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.kernel32.VirtualAlloc.restype = ctypes.c_void_p
        self.kernel32.VirtualAlloc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self.kernel32.VirtualFree.restype = ctypes.c_int
        self.kernel32.VirtualFree.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
        ]
        self.MEM_COMMIT = 0x1000
        self.MEM_RESERVE = 0x2000
        self.MEM_RELEASE = 0x8000
        self.PAGE_READWRITE = 0x04

    def alloc(self, size: int) -> int:
        addr = self.kernel32.VirtualAlloc(
            None,
            size,
            self.MEM_COMMIT | self.MEM_RESERVE,
            self.PAGE_READWRITE,
        )
        addr_int = int(addr) if addr is not None else 0
        if addr_int == 0:
            err = ctypes.get_last_error()
            raise OSError(err, f"VirtualAlloc failed: GetLastError={err}")
        return addr_int

    def free(self, addr: int, size: int) -> None:
        ok = self.kernel32.VirtualFree(ctypes.c_void_p(addr), 0, self.MEM_RELEASE)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, f"VirtualFree failed: GetLastError={err}")


def _make_allocator() -> Any:
    if sys.platform.startswith("win"):
        return _WindowsAllocator()
    return _PosixAllocator()


# ---------------------------------------------------------------------------
# Field encoding / decoding
# ---------------------------------------------------------------------------


_STRUCT_SCALAR = {
    "u8": "<B",
    "u32": "<I",
    "u64": "<Q",
    "f32": "<f",
    "pointer": "<Q",  # 64-bit absolute address stored as little-endian u64.
}


def _encode_scalar(field_type: str, value: Any) -> bytes:
    if field_type in _STRUCT_SCALAR:
        return struct.pack(_STRUCT_SCALAR[field_type], value)
    raise ValueError(f"unsupported scalar type: {field_type}")


def _decode_scalar(field_type: str, raw: bytes) -> Any:
    if field_type in _STRUCT_SCALAR:
        return struct.unpack(_STRUCT_SCALAR[field_type], raw)[0]
    raise ValueError(f"unsupported scalar type: {field_type}")


def _write_bytes(addr: int, data: bytes) -> None:
    ctypes.memmove(addr, data, len(data))


def _read_bytes(addr: int, size: int) -> bytes:
    buf = (ctypes.c_ubyte * size)()
    ctypes.memmove(buf, addr, size)
    return bytes(buf)


# ---------------------------------------------------------------------------
# Fixture state
# ---------------------------------------------------------------------------


class _FixtureState:
    def __init__(self, layout: Layout, control_lies: dict[str, int]) -> None:
        self.layout = layout
        self.control_lies = control_lies
        from ...memory_engine.protocol import read_process_start_time_ns
        self.start_time_ns = read_process_start_time_ns(os.getpid())
        if self.start_time_ns <= 0:
            raise RuntimeError('fixture process birth identity unavailable')
        self.module_fingerprint = self._compute_fingerprint()
        self.allocator = _make_allocator()
        self.regions: dict[str, tuple[int, int]] = {}
        for i in range(layout.region_count):
            name = f"region_{i}"
            base = self.allocator.alloc(layout.region_size)
            self.regions[name] = (base, layout.region_size)
        self._initialise_fields()

    # --- lifecycle ------------------------------------------------------

    def shutdown(self) -> None:
        for name, (base, size) in list(self.regions.items()):
            try:
                self.allocator.free(base, size)
            except OSError:
                pass
            self.regions.pop(name, None)

    # --- helpers --------------------------------------------------------

    def _compute_fingerprint(self) -> str:
        with open(__file__, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def _addr_of(self, field: FieldSpec) -> int:
        base, _ = self.regions[field.region]
        return base + field.offset

    def _initialise_fields(self) -> None:
        for field in self.layout.fields:
            addr = self._addr_of(field)
            if field.type == "pointer":
                assert field.pointer_to_region is not None
                assert field.pointer_to_offset is not None
                target_base, _ = self.regions[field.pointer_to_region]
                pointer_value = target_base + field.pointer_to_offset
                _write_bytes(addr, _encode_scalar("pointer", pointer_value))
            elif field.type == "u32_array":
                # Seed enemies: id = 1000+i, hp = 100 - i*10.
                payload = bytearray()
                entries = field.length // 2  # id+hp pairs
                for i in range(entries):
                    payload += struct.pack("<I", 1000 + i)
                    payload += struct.pack("<I", 100 - i * 10)
                _write_bytes(addr, bytes(payload))
            else:
                if field.initial is None:
                    continue
                _write_bytes(addr, _encode_scalar(field.type, field.initial))

    # --- RPC handlers ---------------------------------------------------

    def identity(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "start_time_ns": self.start_time_ns,
            "module_fingerprint": self.module_fingerprint,
            "region_map": [
                {"name": name, "base": f"0x{base:X}", "size": size}
                for name, (base, size) in self.regions.items()
            ],
            "layout": self.layout.name,
        }

    def get(self, field_name: str) -> Any:
        if field_name in self.control_lies:
            return self.control_lies[field_name]
        field = self.layout.field(field_name)
        addr = self._addr_of(field)
        if field.type == "u32_array":
            raw = _read_bytes(addr, field.width)
            return [struct.unpack_from("<I", raw, o)[0] for o in range(0, field.width, 4)]
        raw = _read_bytes(addr, field.width)
        return _decode_scalar(field.type, raw)

    def set(self, field_name: str, value: Any) -> None:
        field = self.layout.field(field_name)
        addr = self._addr_of(field)
        if field.type == "u32_array":
            if not isinstance(value, (list, tuple)):
                raise ValueError("u32_array set requires a list of ints")
            payload = b"".join(struct.pack("<I", int(v)) for v in value)
            if len(payload) != field.width:
                raise ValueError(f"u32_array length mismatch: want {field.width} bytes, got {len(payload)}")
            _write_bytes(addr, payload)
            return
        if field.type == "pointer":
            _write_bytes(addr, _encode_scalar("pointer", int(value)))
            return
        _write_bytes(addr, _encode_scalar(field.type, value))

    def move(self, region_names: list[str]) -> list[dict[str, Any]]:
        # Snapshot every field's bytes before moving so we can rewrite them
        # at the new base.
        snapshots: dict[str, bytes] = {}
        for field in self.layout.fields:
            if field.region in region_names:
                addr = self._addr_of(field)
                snapshots[field.name] = _read_bytes(addr, field.width)
        # Also snapshot every field whose *target* is in region_names so we
        # can fix up pointer values that reference a moved region.
        for region in region_names:
            if region not in self.regions:
                raise ValueError(f"unknown region: {region!r}")
            old_base, size = self.regions[region]
            # Try several times to actually land at a different base so the
            # test gets re-randomisation it can observe. Modern allocators
            # usually reuse the freed slot; allocating the *new* region
            # before freeing the old one forces a different address.
            new_base = self.allocator.alloc(size)
            self.allocator.free(old_base, size)
            self.regions[region] = (new_base, size)
        # Replay the snapshotted bytes at the new address.
        for field in self.layout.fields:
            if field.name in snapshots:
                addr = self._addr_of(field)
                _write_bytes(addr, snapshots[field.name])
        # Fix up pointer fields whose target region moved.
        for field in self.layout.fields:
            if field.type == "pointer" and field.pointer_to_region in region_names:
                assert field.pointer_to_region is not None
                assert field.pointer_to_offset is not None
                target_base, _ = self.regions[field.pointer_to_region]
                pointer_value = target_base + field.pointer_to_offset
                _write_bytes(self._addr_of(field), _encode_scalar("pointer", pointer_value))
        return [
            {"name": name, "base": f"0x{base:X}", "size": size}
            for name, (base, size) in self.regions.items()
        ]


# ---------------------------------------------------------------------------
# RPC dispatch
# ---------------------------------------------------------------------------


def _dispatch(state: _FixtureState, request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    if method == "identity":
        return state.identity()
    if method == "get":
        field = request.get("field")
        if not isinstance(field, str):
            return {"error": "get requires 'field' as string"}
        return {"value": state.get(field)}
    if method == "set":
        field = request.get("field")
        value = request.get("value")
        if not isinstance(field, str):
            return {"error": "set requires 'field' as string"}
        state.set(field, value)
        return {"ok": True}
    if method == "move":
        regions = request.get("regions")
        if not isinstance(regions, list) or not all(isinstance(r, str) for r in regions):
            return {"error": "move requires 'regions' as list[str]"}
        return {"new_region_map": state.move(regions)}
    if method == "shutdown":
        return {"ok": True, "__shutdown": True}
    return {"error": f"unknown method: {method!r}"}


def _parse_control_lies(spec: str | None) -> dict[str, int]:
    if not spec:
        return {}
    out: dict[str, int] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"malformed --control-lie entry: {pair!r}")
        name, raw = pair.split(":", 1)
        out[name.strip()] = int(raw.strip())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Disposable fixture target")
    parser.add_argument("--layout", default="DEFAULT")
    parser.add_argument(
        "--control-lie",
        default=None,
        help="Comma-separated field:int pairs; get() returns the lie regardless of memory.",
    )
    parser.add_argument("--regions", type=int, default=None)
    args = parser.parse_args(argv)

    if args.layout != "DEFAULT":
        print(f"[fixture] only DEFAULT layout is supported, got {args.layout!r}", file=sys.stderr, flush=True)
        return 2

    layout = DEFAULT_LAYOUT
    if args.regions is not None:
        layout = Layout(
            name=layout.name,
            region_count=args.regions,
            region_size=layout.region_size,
            fields=layout.fields,
        )
    lies = _parse_control_lies(args.control_lie)
    try:
        state = _FixtureState(layout, lies)
    except Exception as exc:  # pragma: no cover - allocator failures are fatal
        print(f"[fixture] boot failed: {exc!r}", file=sys.stderr, flush=True)
        return 3

    print(f"[fixture] ready pid={os.getpid()} layout={layout.name}", file=sys.stderr, flush=True)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except Exception as exc:
                sys.stdout.write(json.dumps({"error": f"malformed json: {exc}"}) + "\n")
                sys.stdout.flush()
                continue
            try:
                response = _dispatch(state, request)
            except Exception as exc:
                response = {"error": f"handler raised: {exc!r}"}
            shutdown_requested = bool(response.pop("__shutdown", False))
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            if shutdown_requested:
                break
    finally:
        state.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
