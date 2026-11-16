"""Windows adapter: ``ReadProcessMemory`` / ``WriteProcessMemory``.

The module imports cleanly on non-Windows hosts (so the engine can be
unit-tested on macOS or Linux without conditional imports elsewhere);
construction on the wrong host raises
:class:`MemoryEngineUnavailable('platform != windows')`.

Pointer-width-correct signatures:

* ``HANDLE`` and ``LPCVOID`` are ``c_void_p`` (8 bytes on 64-bit).
* ``SIZE_T`` is ``c_size_t`` — NOT ``c_uint32``. A 32-bit mistake here
  would silently truncate a > 4 GiB count. The previous implementation
  the task flagged used ``c_uint32`` and would corrupt large reads.

Handles are released in :meth:`close` and in ``__exit__``. Partial
reads are surfaced as structured :class:`PartialReadError` so callers
can distinguish 'page not mapped' from 'cross-region boundary'.
"""
from __future__ import annotations

import ctypes
import struct
import sys
from dataclasses import dataclass
from typing import Any

from .protocol import (
    MemoryEngineError,
    MemoryEngineUnavailable,
    PartialReadError,
    ProcessIdentity,
)
from .ownership import ProcessLease


# Constants from winnt.h / processthreadsapi.h
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40


# ---------------------------------------------------------------------------
# Bindings (lazy)
# ---------------------------------------------------------------------------


class _WinApi:
    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise MemoryEngineUnavailable("platform != windows")
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        except OSError as exc:  # pragma: no cover
            raise MemoryEngineUnavailable(f"kernel32 not loadable: {exc}") from exc

        # OpenProcess(dwDesiredAccess: DWORD, bInheritHandle: BOOL, dwProcessId: DWORD) -> HANDLE
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]

        self.kernel32.CloseHandle.restype = ctypes.c_int
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        # ReadProcessMemory(hProcess, lpBaseAddress, lpBuffer, nSize: SIZE_T, *lpNumberOfBytesRead: SIZE_T) -> BOOL
        self.kernel32.ReadProcessMemory.restype = ctypes.c_int
        self.kernel32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]

        self.kernel32.WriteProcessMemory.restype = ctypes.c_int
        self.kernel32.WriteProcessMemory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]

        # VirtualQueryEx(hProcess, lpAddress, *MEMORY_BASIC_INFORMATION, dwLength: SIZE_T) -> SIZE_T
        self.kernel32.VirtualQueryEx.restype = ctypes.c_size_t
        self.kernel32.VirtualQueryEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]

        self.kernel32.GetExitCodeProcess.restype = ctypes.c_int
        self.kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]

        # GetProcessTimes for birth identity.
        self.kernel32.GetProcessTimes.restype = ctypes.c_int
        self.kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint64),  # lpCreationTime (FILETIME)
            ctypes.POINTER(ctypes.c_uint64),  # lpExitTime
            ctypes.POINTER(ctypes.c_uint64),  # lpKernelTime
            ctypes.POINTER(ctypes.c_uint64),  # lpUserTime
        ]


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_uint32),
        ("PartitionId", ctypes.c_uint16),
        ("_pad0", ctypes.c_uint16),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
    ]


_SCALAR_FMT = {
    "int": ("<i", 4),
    "uint": ("<I", 4),
    "int16": ("<h", 2),
    "int64": ("<q", 8),
    "uint64": ("<Q", 8),
    "float": ("<f", 4),
    "double": ("<d", 8),
    "byte": ("<b", 1),
    "ubyte": ("<B", 1),
}


@dataclass
class WinAdapterInfo:
    pid: int
    identity: ProcessIdentity


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class WindowsMemoryAdapter:
    """Pointer-width-correct RPM/WPM/VQE adapter."""

    def __init__(
        self,
        pid: int,
        *,
        identity: ProcessIdentity | None = None,
        access: int = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
    ) -> None:
        self._api = _WinApi()
        self._pid = int(pid)
        self._handle = ctypes.c_void_p(self._api.kernel32.OpenProcess(ctypes.c_uint32(access), 0, ctypes.c_uint32(self._pid)))
        if not self._handle.value:
            err = ctypes.get_last_error()
            raise MemoryEngineError(f"OpenProcess(pid={pid}) failed: GetLastError={err}")

        self._lease = None
        try:
            birth = self._birth()
            if identity is not None and (identity.pid != self._pid or identity.start_time_ns != birth):
                raise MemoryEngineError("process birth identity mismatch")
            self._identity = identity or ProcessIdentity(pid=self._pid, start_time_ns=birth)
            self._lease = ProcessLease(self._pid, birth)
            self._ensure_live()
        except BaseException:
            self.close()
            raise

    def _birth(self):
        creation, exit_t, kernel_t, user_t = (ctypes.c_uint64() for _ in range(4))
        if not self._api.kernel32.GetProcessTimes(self._handle, ctypes.byref(creation),
                ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t)):
            raise MemoryEngineError("GetProcessTimes failed; process birth identity unavailable")
        return (creation.value - 116444736000000000) * 100

    def _ensure_live(self):
        status = ctypes.c_uint32()
        if (not self._handle.value or
            not self._api.kernel32.GetExitCodeProcess(self._handle, ctypes.byref(status)) or
            status.value != 259 or self._birth() != self._identity.start_time_ns):
            self.close()
            raise MemoryEngineError("target closed, exited or process birth identity changed")

    @property
    def info(self) -> WinAdapterInfo:
        return WinAdapterInfo(pid=self._pid, identity=self._identity)

    @property
    def identity(self) -> ProcessIdentity:
        return self._identity

    # Raw bytes --------------------------------------------------------------

    def read_bytes(self, address: int, size: int) -> bytes:
        self._ensure_live()
        if not 0 <= int(address) < 2**64:
            raise MemoryEngineError("address outside unsigned 64-bit range")
        if not 0 <= size <= 4 * 1024 * 1024:
            raise MemoryEngineError("read size exceeds the 4 MiB operation limit")
        if size <= 0:
            return b""
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = self._api.kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(int(address)),
            buf,
            ctypes.c_size_t(int(size)),
            ctypes.byref(read),
        )
        got = int(read.value)
        if not ok or got < size:
            err = ctypes.get_last_error()
            raise PartialReadError(
                address=int(address),
                requested=int(size),
                got=got,
                reason=f"ReadProcessMemory GetLastError={err}",
            )
        return bytes(buf[:got])

    def write_bytes(self, address: int, data: bytes, *, verify: bool = True) -> None:
        self._ensure_live()
        if not 0 <= int(address) < 2**64:
            raise MemoryEngineError("address outside unsigned 64-bit range")
        payload = bytes(data)
        if len(payload) > 4 * 1024 * 1024:
            raise MemoryEngineError("write size exceeds the 4 MiB operation limit")
        buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        written = ctypes.c_size_t(0)
        ok = self._api.kernel32.WriteProcessMemory(
            self._handle,
            ctypes.c_void_p(int(address)),
            buf,
            ctypes.c_size_t(len(payload)),
            ctypes.byref(written),
        )
        if not ok or int(written.value) != len(payload):
            err = ctypes.get_last_error()
            raise MemoryEngineError(
                f"WriteProcessMemory({address:#x}, {len(payload)}) short write: "
                f"got {int(written.value)}, GetLastError={err}"
            )
        if verify:
            readback = self.read_bytes(int(address), len(payload))
            if readback != payload:
                raise MemoryEngineError(
                    f"write at {address:#x} did not verify: wrote {payload!r} readback {readback!r}"
                )

    def read_value(self, address: int, vtype: str = "int") -> Any:
        fmt, size = _SCALAR_FMT[vtype]
        return struct.unpack(fmt, self.read_bytes(address, size))[0]

    def write_value(self, address: int, value: Any, vtype: str = "int", *, verify: bool = True) -> None:
        fmt, _ = _SCALAR_FMT[vtype]
        if vtype in ("float", "double"):
            value = float(value)
        else:
            value = int(value)
        self.write_bytes(address, struct.pack(fmt, value), verify=verify)

    # VirtualQueryEx wrapper -------------------------------------------------

    def query_region(self, address: int) -> dict[str, Any]:
        self._ensure_live()
        mbi = _MemoryBasicInformation()
        got = self._api.kernel32.VirtualQueryEx(
            self._handle,
            ctypes.c_void_p(int(address)),
            ctypes.byref(mbi),
            ctypes.c_size_t(ctypes.sizeof(mbi)),
        )
        if not got:
            err = ctypes.get_last_error()
            raise MemoryEngineError(f"VirtualQueryEx failed: GetLastError={err}")
        return {
            "base": int(mbi.BaseAddress or 0),
            "allocation_base": int(mbi.AllocationBase or 0),
            "size": int(mbi.RegionSize),
            "state": int(mbi.State),
            "protect": int(mbi.Protect),
            "type": int(mbi.Type),
            "allocation_protect": int(mbi.AllocationProtect),
        }

    # Lifetime ---------------------------------------------------------------

    def close(self) -> None:
        if getattr(self, "_lease", None) is not None:
            self._lease.close()
            self._lease = None
        if getattr(self, "_handle", None) and self._handle.value:
            try:
                self._api.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = ctypes.c_void_p(0)

    def __enter__(self) -> "WindowsMemoryAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:  # best-effort
        try:
            self.close()
        except Exception:
            pass


__all__ = ["WindowsMemoryAdapter", "WinAdapterInfo"]
