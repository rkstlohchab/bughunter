"""MemoryReader backend using MemProcFS over PCILeech DMA.

Thin wrapper so all engine walkers see the same interface regardless of
whether the memory comes from DMA, external RPM, or an Android bridge.
"""
from __future__ import annotations

from typing import Optional

from .engines.base import MemoryReader


class MemProcFsReader(MemoryReader):
    def __init__(self, *, process_name: str, device: str = "fpga://"):
        import memprocfs                       # noqa: WPS433 — optional dep
        self.vmm = memprocfs.Vmm(["-device", device])
        proc = None
        for p in self.vmm.process_list():
            if p.name.lower() == process_name.lower():
                proc = p; break
        if proc is None:
            raise RuntimeError(f"process not found: {process_name}")
        self.proc = proc
        self._modules: dict[str, int] = {}
        for m in proc.module_list():
            self._modules[m.name.lower()] = m.base

    def read(self, address: int, size: int) -> bytes:
        return self.vmm.memory.read(self.proc.pid, address, size)

    def resolve_module(self, name: str) -> int:
        return self._modules.get(name.lower(), 0)


class WindowsRpmReader(MemoryReader):
    """External RPM via OpenProcess + ReadProcessMemory. Windows only."""

    def __init__(self, *, process_name: str):
        import ctypes
        import ctypes.wintypes as wt           # noqa: WPS433 — optional dep
        self.ct = ctypes
        self.wt = wt
        self.k32 = ctypes.windll.kernel32
        self.psapi = ctypes.windll.psapi
        pid = self._find_pid(process_name)
        if not pid:
            raise RuntimeError(f"process not found: {process_name}")
        PROCESS_VM_READ = 0x0010
        PROCESS_QUERY_INFORMATION = 0x0400
        self.handle = self.k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise RuntimeError(f"OpenProcess failed pid={pid}")
        self.pid = pid
        self._modules: dict[str, int] = {}
        self._enum_modules()

    def _find_pid(self, name: str) -> Optional[int]:
        TH32CS_SNAPPROCESS = 0x00000002
        class PE32(self.ct.Structure):
            _fields_ = [
                ("dwSize", self.wt.DWORD),
                ("cntUsage", self.wt.DWORD),
                ("th32ProcessID", self.wt.DWORD),
                ("th32DefaultHeapID", self.ct.POINTER(self.ct.c_ulong)),
                ("th32ModuleID", self.wt.DWORD),
                ("cntThreads", self.wt.DWORD),
                ("th32ParentProcessID", self.wt.DWORD),
                ("pcPriClassBase", self.ct.c_long),
                ("dwFlags", self.wt.DWORD),
                ("szExeFile", self.ct.c_wchar * 260),
            ]
        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PE32(); entry.dwSize = self.ct.sizeof(entry)
        if not self.k32.Process32FirstW(snap, self.ct.byref(entry)):
            return None
        target = name.lower()
        while True:
            if entry.szExeFile.lower() == target:
                self.k32.CloseHandle(snap)
                return entry.th32ProcessID
            if not self.k32.Process32NextW(snap, self.ct.byref(entry)):
                break
        self.k32.CloseHandle(snap)
        return None

    def _enum_modules(self) -> None:
        count = (self.wt.DWORD * 1024)()
        needed = self.wt.DWORD(0)
        if not self.psapi.EnumProcessModulesEx(
            self.handle, count, self.ct.sizeof(count),
                self.ct.byref(needed), 0x03):
            return
        n = needed.value // self.ct.sizeof(self.wt.HMODULE)
        for i in range(n):
            mod = self.wt.HMODULE(count[i])
            name = (self.ct.c_wchar * 260)()
            if self.psapi.GetModuleBaseNameW(self.handle, mod, name, 260):
                self._modules[name.value.lower()] = count[i]

    def read(self, address: int, size: int) -> bytes:
        buf = (self.ct.c_ubyte * size)()
        got = self.wt.SIZE_T(0)
        ok = self.k32.ReadProcessMemory(self.handle, address, buf, size,
                                          self.ct.byref(got))
        if not ok or got.value != size:
            return bytes(buf[:got.value]) if got.value else b""
        return bytes(buf)

    def resolve_module(self, name: str) -> int:
        return self._modules.get(name.lower(), 0)
