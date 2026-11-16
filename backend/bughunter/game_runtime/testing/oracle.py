"""Independent memory oracle for the disposable fixture target.

The oracle NEVER calls :meth:`Fixture.get` to answer a value question.
It takes an :class:`~fixture_target.IdentityResponse` once (to learn the
region bases) and then reads the running child's memory through an OS
code path that is deliberately separate from the fixture's own code:

* **Linux** — ``open('/proc/<pid>/mem', 'rb')`` + ``pread``.
* **macOS** — ``mach_vm_read_overwrite`` via ``task_for_pid`` first; when
  that returns ``KERN_FAILURE`` (the common case on dev machines without
  the ``com.apple.security.cs.debugger`` entitlement) the oracle falls
  back to a subprocess ``lldb --batch -p <pid>`` reader. ``lldb`` ships
  signed with the debugger entitlement, so it can attach to any process
  the current user owns. Both paths read the child's memory through
  machinery entirely separate from ``fixture_process.py``, so the
  independence property holds under either path. If neither works,
  :class:`OracleUnavailable` is raised with the reason.
* **Windows** — ``ReadProcessMemory`` via ``OpenProcess(PROCESS_VM_READ)``.

Field decoding is driven by the shared :data:`layout.DEFAULT_LAYOUT`,
so the oracle agrees with the fixture on *where* a field lives but
reads *what* it contains directly from the OS.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import struct
import subprocess
import sys
from typing import Any

from .fixture_target import IdentityResponse, RegionInfo
from .layout import DEFAULT_LAYOUT, FieldSpec, Layout


class OracleUnavailable(RuntimeError):
    """Raised when no usable OS read path is available on this platform."""


# ---------------------------------------------------------------------------
# Platform-specific readers
# ---------------------------------------------------------------------------


class _LinuxReader:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        try:
            # ``O_RDONLY`` is enough; the file supports pread.
            self._fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        except FileNotFoundError as exc:
            raise OracleUnavailable(f"/proc/{pid}/mem does not exist: {exc}") from exc
        except PermissionError as exc:
            raise OracleUnavailable(
                f"/proc/{pid}/mem refused (yama ptrace_scope or missing ptrace permission): {exc}"
            ) from exc

    def read(self, addr: int, size: int) -> bytes:
        try:
            return os.pread(self._fd, size, addr)
        except OSError as exc:
            raise OracleUnavailable(
                f"pread({addr:#x}, {size}) failed on /proc/{self.pid}/mem: {exc}"
            ) from exc

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


class _MacReader:
    """``mach_vm_read_overwrite`` reader. Requires debugger entitlement."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        try:
            libc_path = ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib"
            self.libc = ctypes.CDLL(libc_path, use_errno=True)
        except OSError as exc:  # pragma: no cover
            raise OracleUnavailable(f"libc not loadable: {exc}") from exc

        mach_port_t = ctypes.c_uint

        self.libc.task_for_pid.restype = ctypes.c_int
        self.libc.task_for_pid.argtypes = [mach_port_t, ctypes.c_int, ctypes.POINTER(mach_port_t)]

        self.libc.mach_vm_read_overwrite.restype = ctypes.c_int
        self.libc.mach_vm_read_overwrite.argtypes = [
            mach_port_t,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.c_ulonglong),
        ]

        try:
            self._mach_task_self = mach_port_t.in_dll(self.libc, "mach_task_self_").value
        except ValueError as exc:
            raise OracleUnavailable(f"mach_task_self_ symbol not resolvable: {exc}") from exc

        self._task = mach_port_t()
        kr = self.libc.task_for_pid(self._mach_task_self, int(pid), ctypes.byref(self._task))
        if kr != 0:
            raise OracleUnavailable(
                "task_for_pid failed with kern_return "
                f"{kr}; macOS requires com.apple.security.cs.debugger entitlement "
                "or root for this call"
            )

    def read(self, addr: int, size: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        out_size = ctypes.c_ulonglong(0)
        kr = self.libc.mach_vm_read_overwrite(
            self._task,
            ctypes.c_ulonglong(addr),
            ctypes.c_ulonglong(size),
            ctypes.c_ulonglong(ctypes.addressof(buf)),
            ctypes.byref(out_size),
        )
        if kr != 0:
            raise OracleUnavailable(
                f"mach_vm_read_overwrite({addr:#x}, {size}) failed: kern_return={kr}"
            )
        return bytes(buf[: int(out_size.value)])

    def close(self) -> None:
        pass


class _LldbReader:
    """macOS fallback. Spawns ``lldb --batch`` per read, attaches to the
    fixture's pid, runs ``lldb.process.ReadMemory``, detaches, exits.

    ``lldb`` is Apple-signed with the debugger entitlement, so it can
    attach to user-owned processes where our in-process
    ``task_for_pid`` call is refused. The read path is entirely outside
    ``fixture_process.py``'s own code, so independence is preserved.
    """

    _READ_TIMEOUT_S = 15.0

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._lldb = shutil.which("lldb")
        if not self._lldb:
            raise OracleUnavailable(
                "lldb not on PATH; install Xcode Command Line Tools"
            )
        probe = subprocess.run(
            [
                self._lldb, "--batch", "-p", str(pid),
                "-o", "script import lldb; print('ATTACH_OK' if lldb.process.IsValid() else 'ATTACH_FAIL')",
                "-o", "detach",
                "-o", "quit",
            ],
            capture_output=True, text=True, timeout=self._READ_TIMEOUT_S,
        )
        if "ATTACH_OK" not in (probe.stdout or ""):
            raise OracleUnavailable(
                f"lldb cannot attach to pid={pid}; "
                f"stdout={(probe.stdout or '')[:200]!r} stderr={(probe.stderr or '')[:200]!r}"
            )

    def read(self, addr: int, size: int) -> bytes:
        script = (
            "script import lldb; "
            "e=lldb.SBError(); "
            f"d=lldb.process.ReadMemory({addr}, {size}, e); "
            "print('OK:'+d.hex() if e.Success() else 'ERR:'+(e.GetCString() or 'nil'))"
        )
        proc = subprocess.run(
            [
                self._lldb, "--batch", "-p", str(self.pid),
                "-o", script,
                "-o", "detach",
                "-o", "quit",
            ],
            capture_output=True, text=True, timeout=self._READ_TIMEOUT_S,
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("OK:"):
                return bytes.fromhex(line[3:])
            if line.startswith("ERR:"):
                raise OracleUnavailable(
                    f"lldb read {addr:#x} size={size} failed: {line[4:]}"
                )
        raise OracleUnavailable(
            f"lldb read {addr:#x} size={size} produced no OK/ERR marker; "
            f"stdout={(proc.stdout or '')[:300]!r}"
        )

    def close(self) -> None:
        pass


class _WindowsReader:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        self.kernel32.CloseHandle.restype = ctypes.c_int
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.ReadProcessMemory.restype = ctypes.c_int
        self.kernel32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        PROCESS_VM_READ = 0x0010
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = self.kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
            False,
            int(pid),
        )
        if not handle:
            err = ctypes.get_last_error()
            raise OracleUnavailable(
                f"OpenProcess(PROCESS_VM_READ, pid={pid}) failed: GetLastError={err}"
            )
        self._handle = handle

    def read(self, addr: int, size: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = self.kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(addr),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OracleUnavailable(
                f"ReadProcessMemory({addr:#x}, {size}) failed: GetLastError={err}"
            )
        return bytes(buf[: int(read.value)])

    def close(self) -> None:
        try:
            self.kernel32.CloseHandle(self._handle)
        except Exception:
            pass


def _make_reader(pid: int):  # -> _LinuxReader | _MacReader | _LldbReader | _WindowsReader
    if sys.platform == "linux":
        return _LinuxReader(pid)
    if sys.platform == "darwin":
        try:
            return _MacReader(pid)
        except OracleUnavailable as mach_err:
            try:
                return _LldbReader(pid)
            except OracleUnavailable as lldb_err:
                raise OracleUnavailable(
                    f"macOS: mach reader denied ({mach_err}); "
                    f"lldb fallback also failed ({lldb_err}). "
                    "Enable the com.apple.security.cs.debugger entitlement, "
                    "run as root, or install Xcode Command Line Tools."
                ) from lldb_err
    if sys.platform.startswith("win"):
        return _WindowsReader(pid)
    raise OracleUnavailable(f"no oracle implementation for platform {sys.platform!r}")


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


_STRUCT_SCALAR = {
    "u8": "<B",
    "u32": "<I",
    "u64": "<Q",
    "f32": "<f",
    "pointer": "<Q",
}


class Oracle:
    """Reads fixture memory directly via the host OS.

    Construction performs the one capability check that matters (open the
    mem device, grab the task port, open the process handle). Failures
    there raise :class:`OracleUnavailable` with a reason string; callers
    should propagate that as a pytest skip when the test depends on it.
    """

    def __init__(
        self,
        pid: int,
        region_map: list[RegionInfo] | list[dict[str, Any]],
        layout: Layout = DEFAULT_LAYOUT,
    ) -> None:
        self.pid = int(pid)
        self.layout = layout
        self.regions: dict[str, RegionInfo] = {}
        for entry in region_map:
            if isinstance(entry, RegionInfo):
                self.regions[entry.name] = entry
            else:
                info = RegionInfo.from_wire(entry)
                self.regions[info.name] = info
        self._reader = _make_reader(self.pid)

    # --- factories -------------------------------------------------------

    @classmethod
    def from_identity(cls, identity: IdentityResponse, layout: Layout = DEFAULT_LAYOUT) -> "Oracle":
        return cls(pid=identity.pid, region_map=identity.region_map, layout=layout)

    # --- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> "Oracle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- read primitives -------------------------------------------------

    def read_bytes(self, addr: int, size: int) -> bytes:
        return self._reader.read(int(addr), int(size))

    def read_field(self, field_name: str) -> Any:
        field = self.layout.field(field_name)
        return self._read_field_spec(field)

    def read_field_at(self, field_name: str, region_bases: dict[str, int]) -> Any:
        """Read ``field_name`` given a possibly-different map of region bases.

        Used by the restart harness to probe whether a stale recipe still
        resolves after re-randomised bases.
        """
        field = self.layout.field(field_name)
        base = region_bases[field.region]
        return self._decode_field(field, self.read_bytes(base + field.offset, field.width))

    def read_absolute(self, abs_addr: int, field_name: str) -> Any:
        """Read ``field_name`` from a hard-coded absolute address."""
        field = self.layout.field(field_name)
        return self._decode_field(field, self.read_bytes(abs_addr, field.width))

    # --- internals -------------------------------------------------------

    def _read_field_spec(self, field: FieldSpec) -> Any:
        region = self.regions.get(field.region)
        if region is None:
            raise OracleUnavailable(f"oracle has no region named {field.region!r}")
        return self._decode_field(field, self.read_bytes(region.base + field.offset, field.width))

    def _decode_field(self, field: FieldSpec, raw: bytes) -> Any:
        if field.type == "u32_array":
            return [struct.unpack_from("<I", raw, o)[0] for o in range(0, field.width, 4)]
        fmt = _STRUCT_SCALAR.get(field.type)
        if fmt is None:
            raise OracleUnavailable(f"oracle cannot decode field type {field.type!r}")
        return struct.unpack(fmt, raw)[0]


__all__ = ["Oracle", "OracleUnavailable"]
