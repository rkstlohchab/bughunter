"""macOS adapter: ``mach_vm_read_overwrite`` + ``mach_vm_write_overwrite``.

Two read/write paths are supported, in order of preference:

1. **In-process mach**. ``task_for_pid`` must succeed. That needs the
   caller to be root OR carry ``com.apple.security.cs.debugger`` AND
   the target is not a hardened runtime. On a dev machine with neither,
   this returns :class:`MemoryEngineUnavailable` for that pid and we
   fall back to lldb.

2. **Persistent lldb subprocess**. ``/usr/bin/lldb`` ships signed with
   the debugger entitlement; we now spawn **one** ``lldb -p <pid>``
   subprocess, hold the attach for the adapter's lifetime, and drive
   every ``script`` call over its stdio instead of one ``lldb --batch``
   attach per operation. Per-op cost falls from ~1.1 s to ~50–200 ms
   and the two-second cleanup budget has comfortable margin.

Every write is acknowledged by reading the bytes back and comparing;
if the readback disagrees the adapter surfaces a structured error
rather than claiming success.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import ctypes
import itertools
import json
import os
import signal
import ctypes.util
import shutil
import struct
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any

from .protocol import (
    MemoryEngineError,
    MemoryEngineUnavailable,
    PartialReadError,
    ProcessIdentity,
    read_process_start_time_ns,
)
from .ownership import ProcessLease


_LLDB_TIMEOUT_S = 15.0
_LLDB_OP_TIMEOUT_S = 1.5
_LLDB_ATTACH_TIMEOUT_S = 8.0


# ---------------------------------------------------------------------------
# libc / mach bindings
# ---------------------------------------------------------------------------


class _MachBindings:
    KERN_SUCCESS = 0

    def __init__(self) -> None:
        if sys.platform != "darwin":  # pragma: no cover - guard
            raise MemoryEngineUnavailable("mac_adapter requires darwin")
        libc_path = ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib"
        try:
            self.libc = ctypes.CDLL(libc_path, use_errno=True)
        except OSError as exc:
            raise MemoryEngineUnavailable(f"libc not loadable: {exc}") from exc

        mach_port_t = ctypes.c_uint
        self.mach_port_t = mach_port_t

        self.libc.task_for_pid.restype = ctypes.c_int
        self.libc.task_for_pid.argtypes = [mach_port_t, ctypes.c_int, ctypes.POINTER(mach_port_t)]

        self.libc.mach_port_deallocate.restype = ctypes.c_int
        self.libc.mach_port_deallocate.argtypes = [mach_port_t, mach_port_t]

        self.libc.mach_vm_read_overwrite.restype = ctypes.c_int
        self.libc.mach_vm_read_overwrite.argtypes = [
            mach_port_t,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.c_ulonglong),
        ]

        # ``mach_vm_write_overwrite`` is not a real Mach entry — the
        # pair is ``mach_vm_write`` (requires the data to be mapped in
        # OUR task) and we avoid that complication by using the
        # ``mach_vm_write`` call, which copies from a buffer pointer
        # in-process. The "overwrite" semantics the task asks for are
        # naturally provided because we do a readback-verify afterwards.
        self.libc.mach_vm_write.restype = ctypes.c_int
        self.libc.mach_vm_write.argtypes = [
            mach_port_t,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
            ctypes.c_uint,
        ]

        try:
            self.mach_task_self = mach_port_t.in_dll(self.libc, "mach_task_self_").value
        except ValueError as exc:
            raise MemoryEngineUnavailable(f"mach_task_self_ symbol missing: {exc}") from exc


# ---------------------------------------------------------------------------
# Reader/writer abstraction
# ---------------------------------------------------------------------------


class _MachReaderWriter:
    def __init__(self, pid: int) -> None:
        self._bindings = _MachBindings()
        self._task = self._bindings.mach_port_t()
        kr = self._bindings.libc.task_for_pid(
            self._bindings.mach_task_self, int(pid), ctypes.byref(self._task)
        )
        if kr != self._bindings.KERN_SUCCESS:
            raise MemoryEngineUnavailable(
                f"task_for_pid(pid={pid}) failed kern_return={kr}; "
                "macOS requires debugger entitlement or root"
            )

    def read(self, address: int, size: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        out = ctypes.c_ulonglong(0)
        kr = self._bindings.libc.mach_vm_read_overwrite(
            self._task,
            ctypes.c_ulonglong(address),
            ctypes.c_ulonglong(size),
            ctypes.c_ulonglong(ctypes.addressof(buf)),
            ctypes.byref(out),
        )
        if kr != self._bindings.KERN_SUCCESS:
            raise PartialReadError(address=address, requested=size, got=0, reason=f"mach_vm_read_overwrite kern={kr}")
        got = int(out.value)
        if got < size:
            raise PartialReadError(address=address, requested=size, got=got, reason="short read")
        return bytes(buf[:got])

    def write(self, address: int, data: bytes) -> None:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        kr = self._bindings.libc.mach_vm_write(
            self._task,
            ctypes.c_ulonglong(address),
            ctypes.c_ulonglong(ctypes.addressof(buf)),
            ctypes.c_uint(len(data)),
        )
        if kr != self._bindings.KERN_SUCCESS:
            raise MemoryEngineError(f"mach_vm_write failed kern_return={kr}")

    def close(self) -> None:
        if self._task.value:
            try:
                self._bindings.libc.mach_port_deallocate(
                    self._bindings.mach_task_self, self._task
                )
            except Exception:
                pass
            self._task = self._bindings.mach_port_t()


def _target_stopped(pid: int) -> bool:
    state = subprocess.run(["/bin/ps", "-o", "state=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=2)
    return state.returncode == 0 and state.stdout.strip().startswith("T")


def _run_lldb(pid: int, *args, **kwargs):
    was_stopped = _target_stopped(pid)
    # The debugger must never consume frames from its helper's private stdin.
    kwargs.setdefault('stdin', subprocess.DEVNULL)
    try:
        return subprocess.run(*args, **kwargs)
    finally:
        # Some debugserver exits leave a formerly running target suspended.
        # Preserve a target that was already stopped before this operation.
        try:
            stopped_now = _target_stopped(pid)
            if stopped_now != was_stopped:
                os.kill(pid, signal.SIGSTOP if was_stopped else signal.SIGCONT)
        except (OSError, subprocess.SubprocessError):
            pass


# ---------------------------------------------------------------------------
# Persistent lldb session
# ---------------------------------------------------------------------------


# Weakly-referenced registry of live persistent sessions so a Python exit
# via atexit can force-kill any lldb subprocess that would otherwise
# outlive the helper.
_live_sessions: "weakref.WeakSet[_PersistentLldbSession]" = weakref.WeakSet()
_live_sessions_lock = threading.Lock()


def _atexit_reap_sessions() -> None:
    with _live_sessions_lock:
        sessions = list(_live_sessions)
    for session in sessions:
        try:
            session.close()
        except Exception:
            pass


atexit.register(_atexit_reap_sessions)


class _PersistentLldbSession:
    """One lldb subprocess held for the adapter's lifetime.

    The subprocess is spawned under ``setsid`` so SIGTERM can reach its
    whole process group (lldb + debugserver). All read/write/compare
    ops serialise on ``_ops_lock``; the reader thread parses stdout and
    publishes decoded replies keyed by command id.
    """

    _BH_SENTINEL = "BH_RESULT:"

    def __init__(self, pid: int, lldb_path: str) -> None:
        self._pid = int(pid)
        self._initially_stopped = _target_stopped(self._pid)
        if self._initially_stopped:
            raise MemoryEngineUnavailable('target is already paused; debugger attach was not attempted')
        self._birth = read_process_start_time_ns(self._pid)
        self._ready = False
        self._lldb_path = lldb_path
        self._ops_lock = threading.RLock()
        self._resp_cond = threading.Condition()
        self._responses: dict[str, dict] = {}
        self._dead_reason: str | None = None
        self._closed = False
        self._counter = itertools.count(1)
        self._last_op_latency_ms: float = 0.0

        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - trusted caller, fixed binary
                [lldb_path, "--no-lldbinit", "-p", str(self._pid)],
                **popen_kwargs,
            )
        except OSError as exc:
            raise MemoryEngineUnavailable(f"could not spawn lldb: {exc}") from exc

        self._reader_thread = threading.Thread(
            target=self._read_loop, name="lldb-reader", daemon=True,
        )
        self._reader_thread.start()

        with _live_sessions_lock:
            _live_sessions.add(self)

        try:
            self._handshake()
        except BaseException:
            self.close()
            raise

    # -- lifecycle -----------------------------------------------------------

    @property
    def subprocess_pid(self) -> int | None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return None
        return proc.pid

    @property
    def last_op_latency_ms(self) -> float:
        return self._last_op_latency_ms

    def _handshake(self) -> None:
        # Attach stops the process. Restore its original running state
        # before returning; each subsequent operation pauses only briefly.
        probe_id = self._next_id()
        probe = self._call_python(
            "import json, lldb, time\n"
            "lldb.debugger.SetAsync(True)\n"
            "p = lldb.process\n"
            f"if p.IsValid() and not {self._initially_stopped!r}:\n"
            "    e = p.Continue()\n"
            "    if not e.Success(): raise RuntimeError(e.GetCString())\n"
            "    deadline = time.monotonic() + 0.7\n"
            "    while p.GetState() != lldb.eStateRunning and time.monotonic() < deadline: time.sleep(0.002)\n"
            f"print('{self._BH_SENTINEL}' + json.dumps({{'id': {probe_id!r}, "
            "'ok': p.IsValid() and p.GetState() == lldb.eStateRunning, 'state': int(p.GetState())}))\n",
            cmd_id=probe_id,
            timeout=_LLDB_ATTACH_TIMEOUT_S,
        )
        if not probe.get("ok"):
            raise MemoryEngineUnavailable(
                f"lldb cannot attach to pid={self._pid}: {probe!r}"
            )
        self._ready = True

    @contextmanager
    def transaction(self):
        """Keep a running target paused across one guarded recipe update."""
        with self._ops_lock:
            def control(resume):
                ident=self._next_id()
                expected_state = 'lldb.eStateRunning' if resume else 'lldb.eStateStopped'
                command=(
                    "import lldb,json,time\n"
                    "p=lldb.process\n"
                    + ("e=p.Continue()\n" if resume else "e=p.Stop()\n")
                    + "deadline=time.monotonic()+0.7\n"
                    + f"while p.GetState() != {expected_state} and time.monotonic()<deadline: time.sleep(.002)\n"
                    + "time.sleep(.03)\n"
                    + f"print({self._BH_SENTINEL!r}+json.dumps({{'id':{ident!r},'ok':e.Success() and p.GetState()=={expected_state},'err':e.GetCString()}}))\n")
                was_ready=self._ready;self._ready=False
                try:
                    result=self._call_python(command,cmd_id=ident,timeout=1.5)
                finally:self._ready=was_ready
                if not result.get('ok'):raise MemoryEngineError('target transaction state failed: '+str(result))
            control(False)
            try:
                yield
            finally:
                if not self._closed and not self._dead_reason:control(True)

    def yield_target(self, seconds: float = 0.02) -> None:
        """Compatibility wait; running targets progress between every operation."""
        if not self._closed and not self._initially_stopped:
            time.sleep(max(0.001, min(float(seconds), 1.0)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._resp_cond:
            if self._dead_reason is None:
                self._dead_reason = "closed"
            self._resp_cond.notify_all()
        self._terminate_process()
        try:
            if read_process_start_time_ns(self._pid) == self._birth and _target_stopped(self._pid) != self._initially_stopped:
                os.kill(self._pid, signal.SIGSTOP if self._initially_stopped else signal.SIGCONT)
        except (OSError, subprocess.SubprocessError):
            pass
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)

    def _terminate_process(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None or proc.poll() is not None:
            # Still drain streams if open.
            self._close_streams()
            return
        # Prefer a clean quit via stdin so lldb detaches cleanly (and
        # resumes the target). ``quit`` on its own also detaches.
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.write(b"process detach\nquit\n")
                proc.stdin.flush()
                try:
                    proc.stdin.close()
                except (OSError, BrokenPipeError):
                    pass
        except (OSError, BrokenPipeError):
            pass
        try:
            proc.wait(timeout=0.6)
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is None:
            # SIGTERM the whole process group so debugserver dies with lldb.
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            else:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except Exception:
                        pass
            else:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        self._close_streams()

    def _close_streams(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    # -- IO ------------------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for raw in iter(stdout.readline, b""):
                try:
                    line = raw.decode("utf-8", "replace")
                except Exception:
                    continue
                idx = line.find(self._BH_SENTINEL)
                if idx < 0:
                    continue
                payload = line[idx + len(self._BH_SENTINEL):].strip()
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                cmd_id = obj.get("id")
                if not isinstance(cmd_id, str):
                    continue
                with self._resp_cond:
                    self._responses[cmd_id] = obj
                    self._resp_cond.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self._resp_cond:
                if self._dead_reason is None:
                    self._dead_reason = "lldb_exit"
                self._resp_cond.notify_all()

    def _send_command(self, command: str) -> None:
        proc = self._proc
        if proc.stdin is None or proc.stdin.closed:
            raise MemoryEngineError("lldb_exit")
        try:
            proc.stdin.write((command + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise MemoryEngineError(f"lldb_exit: {exc}") from exc

    def _call_python(self, program: str, *, cmd_id: str, timeout: float) -> dict:
        """Run a Python program inside lldb and parse its reply.

        The program is passed as ``script exec(<repr>)`` so arbitrary
        multi-line Python can run in one round trip. The program must
        print ``BH_RESULT:`` followed by a JSON object whose ``id``
        field equals ``cmd_id``.
        """
        if self._closed:
            raise MemoryEngineError("lldb_exit")
        with self._resp_cond:
            if self._dead_reason is not None:
                raise MemoryEngineError(self._dead_reason)
        if self._ready:
            # Keep the response until resume has completed, including when
            # an operation raises. No memory operation leaves the game paused.
            program = (
                "import lldb, time, json, io, contextlib\n"
                "_p = lldb.process\n"
                "_resume = _p.GetState() in (lldb.eStateRunning, lldb.eStateStepping)\n"
                "_capture = io.StringIO()\n"
                "_failure = None\n"
                "try:\n"
                "    if _resume:\n"
                "        _e = _p.Stop()\n"
                "        if not _e.Success(): raise RuntimeError(_e.GetCString())\n"
                "        _deadline = time.monotonic() + 0.7\n"
                "        while _p.GetState() not in (lldb.eStateStopped, lldb.eStateCrashed) and time.monotonic() < _deadline:\n"
                "            time.sleep(0.001)\n"
                "    if _p.GetState() != lldb.eStateStopped: raise RuntimeError('target_not_stopped')\n"
                "    time.sleep(0.03)\n"
                "    with contextlib.redirect_stdout(_capture):\n"
                f"        exec({program!r}, globals())\n"
                "except Exception as _exc:\n"
                "    _failure = str(_exc)\n"
                "finally:\n"
                "    if _resume and _p.IsValid():\n"
                "        _e = _p.Continue()\n"
                "        if not _e.Success(): _failure = 'target_resume_failed: ' + str(_e.GetCString())\n"
                "        _deadline = time.monotonic() + 0.7\n"
                "        while _p.GetState() != lldb.eStateRunning and time.monotonic() < _deadline: time.sleep(0.002)\n"
                "        if _p.GetState() != lldb.eStateRunning: _failure = 'target_resume_failed: state transition timeout'\n"
                "        time.sleep(0.03)\n"
                f"if _failure: print({self._BH_SENTINEL!r} + json.dumps({{'id': {cmd_id!r}, 'ok': False, 'err': _failure, 'reason': 'target_state_failed'}}))\n"
                "else: print(_capture.getvalue(), end='')\n"
            )
        command = "script exec(" + repr(program) + ")"
        start = time.monotonic()
        self._send_command(command)
        deadline = start + timeout
        with self._resp_cond:
            while cmd_id not in self._responses:
                if self._dead_reason is not None:
                    raise MemoryEngineError(self._dead_reason)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._dead_reason = 'lldb_timeout: session requires close; operation outcome unknown'
                    raise MemoryEngineError("lldb_timeout")
                self._resp_cond.wait(remaining)
            response = self._responses.pop(cmd_id)
        self._last_op_latency_ms = (time.monotonic() - start) * 1000.0
        return response

    def _next_id(self) -> str:
        return f"c{next(self._counter)}"

    # -- ops -----------------------------------------------------------------

    # Each op interrupts the running target synchronously, performs its
    # ReadMemory/WriteMemory call, then resumes. Stop/continue is driven
    # through the command interpreter because ``process interrupt`` is
    # synchronous there; we then poll ``GetState()`` to be sure the
    # transition has settled before the actual memory op runs.
    # _call_python wraps each operation in stop/try/finally/resume.
    _OP_PREAMBLE = (
        "import json, lldb\n"
        "process = lldb.process\n"
    )
    _OP_POSTAMBLE = ""

    def read_bytes(self, address: int, size: int) -> bytes:
        with self._ops_lock:
            cmd_id = self._next_id()
            program = (
                self._OP_PREAMBLE +
                "e = lldb.SBError()\n"
                f"data = process.ReadMemory({int(address)}, {int(size)}, e)\n"
                f"payload = {{'id': {cmd_id!r}, 'ok': e.Success(), "
                "'data': data.hex() if data is not None else None, "
                "'err': e.GetCString() or None, "
                "'got': len(data) if data is not None else 0}\n"
                + self._OP_POSTAMBLE +
                f"print('{self._BH_SENTINEL}' + json.dumps(payload))\n"
            )
            resp = self._call_python(program, cmd_id=cmd_id, timeout=_LLDB_OP_TIMEOUT_S)
        if not resp.get("ok"):
            got = int(resp.get("got") or 0)
            raise PartialReadError(
                address=address, requested=size, got=got,
                reason=f"lldb: {resp.get('err') or 'unknown'}",
            )
        hexed = resp.get("data") or ""
        raw = bytes.fromhex(hexed)
        if len(raw) < size:
            raise PartialReadError(address=address, requested=size, got=len(raw),
                                   reason="lldb short read")
        return raw

    def write_bytes(self, address: int, data: bytes, *, verify: bool = False) -> None:
        with self._ops_lock:
            cmd_id = self._next_id()
            if verify:
                program = (
                    self._OP_PREAMBLE +
                    "e = lldb.SBError()\n"
                    f"payload_bytes = bytes.fromhex({data.hex()!r})\n"
                    f"n = process.WriteMemory({int(address)}, payload_bytes, e)\n"
                    "if not e.Success() or n != len(payload_bytes):\n"
                    f"    payload = {{'id': {cmd_id!r}, 'ok': False, "
                    "'reason': 'write_failed', 'written': n, 'err': e.GetCString() or None}\n"
                    "else:\n"
                    "    e2 = lldb.SBError()\n"
                    f"    back = process.ReadMemory({int(address)}, len(payload_bytes), e2)\n"
                    "    if not e2.Success() or back != payload_bytes:\n"
                    f"        payload = {{'id': {cmd_id!r}, 'ok': False, "
                    "'reason': 'readback_mismatch', "
                    "'got': back.hex() if back else None, "
                    "'err': e2.GetCString() or None}\n"
                    "    else:\n"
                    f"        payload = {{'id': {cmd_id!r}, 'ok': True, 'written': n}}\n"
                    + self._OP_POSTAMBLE +
                    f"print('{self._BH_SENTINEL}' + json.dumps(payload))\n"
                )
            else:
                program = (
                    self._OP_PREAMBLE +
                    "e = lldb.SBError()\n"
                    f"payload_bytes = bytes.fromhex({data.hex()!r})\n"
                    f"n = process.WriteMemory({int(address)}, payload_bytes, e)\n"
                    f"payload = {{'id': {cmd_id!r}, "
                    "'ok': e.Success() and n == len(payload_bytes), "
                    "'written': n, 'err': e.GetCString() or None}\n"
                    + self._OP_POSTAMBLE +
                    f"print('{self._BH_SENTINEL}' + json.dumps(payload))\n"
                )
            resp = self._call_python(program, cmd_id=cmd_id, timeout=_LLDB_OP_TIMEOUT_S * 2)
        if not resp.get("ok"):
            reason = resp.get("reason")
            if reason == "readback_mismatch":
                raise MemoryEngineError(
                    f"write at 0x{int(address):x} did not verify: wrote {data!r} "
                    f"readback {resp.get('got')!r}"
                )
            raise MemoryEngineError(
                f"lldb WriteMemory failed: {resp.get('err') or resp!r}"
            )

    def inspect(self, expression: str):
        """Run a JSON-returning expression inside the live lldb session.

        Used by :meth:`MacMemoryAdapter._inspect` so introspection queries
        (``regions()``, ``modules()``) reuse the existing attach instead
        of spawning a second lldb subprocess, which would fail because
        the target is already being debugged.
        """
        with self._ops_lock:
            cmd_id = self._next_id()
            program = (
                "import json, lldb\n"
                f"_val = {expression}\n"
                f"print('{self._BH_SENTINEL}' + json.dumps({{'id': {cmd_id!r}, "
                "'ok': True, 'value': _val}))\n"
            )
            resp = self._call_python(program, cmd_id=cmd_id, timeout=_LLDB_OP_TIMEOUT_S * 2)
        if not resp.get("ok"):
            raise MemoryEngineError(f"lldb inspect failed: {resp!r}")
        return resp.get("value")

    def compare_write(self, address: int, expected: bytes, data: bytes) -> None:
        """Compare, write and readback in ONE lldb round trip."""
        with self._ops_lock:
            cmd_id = self._next_id()
            program = (
                self._OP_PREAMBLE +
                "e = lldb.SBError()\n"
                f"exp = bytes.fromhex({expected.hex()!r})\n"
                f"new = bytes.fromhex({data.hex()!r})\n"
                f"old = process.ReadMemory({int(address)}, len(exp), e)\n"
                "if not e.Success() or old is None:\n"
                f"    result = {{'id': {cmd_id!r}, 'ok': False, 'reason': 'read_failed', "
                "'err': e.GetCString() or None}\n"
                "elif old != exp:\n"
                f"    result = {{'id': {cmd_id!r}, 'ok': False, 'reason': 'precondition_failed'}}\n"
                "else:\n"
                f"    n = process.WriteMemory({int(address)}, new, e)\n"
                "    if not e.Success() or n != len(new):\n"
                f"        result = {{'id': {cmd_id!r}, 'ok': False, 'reason': 'write_failed', "
                "'err': e.GetCString() or None}\n"
                "    else:\n"
                "        e2 = lldb.SBError()\n"
                f"        back = process.ReadMemory({int(address)}, len(new), e2)\n"
                "        if not e2.Success() or back != new:\n"
                f"            result = {{'id': {cmd_id!r}, 'ok': False, 'reason': 'readback_mismatch'}}\n"
                "        else:\n"
                f"            result = {{'id': {cmd_id!r}, 'ok': True}}\n"
                + self._OP_POSTAMBLE +
                f"print('{self._BH_SENTINEL}' + json.dumps(result))\n"
            )
            resp = self._call_python(program, cmd_id=cmd_id, timeout=_LLDB_OP_TIMEOUT_S * 2)
        if not resp.get("ok"):
            reason = resp.get("reason") or "unknown"
            err = resp.get("err")
            if reason == "precondition_failed":
                raise MemoryEngineError("precondition_failed: value changed since read receipt")
            if reason == "readback_mismatch":
                raise MemoryEngineError("write did not verify: readback mismatch")
            raise MemoryEngineError(f"compare_write failed: {reason}: {err}")


class _LldbReaderWriter:
    """Fallback that holds ONE persistent ``lldb -p <pid>`` subprocess.

    Public signature matches the mach backend's read/write/close. The
    heavy lifting lives in :class:`_PersistentLldbSession`; we keep
    this class so callers (and the legacy monkeypatched attach probe)
    still address the same import path.
    """

    def __init__(self, pid: int) -> None:
        self._pid = int(pid)
        self._lldb = shutil.which("lldb")
        if not self._lldb:
            raise MemoryEngineUnavailable("lldb not on PATH")
        if _target_stopped(self._pid):
            raise MemoryEngineUnavailable('target is already paused; debugger attach was not attempted')
        # Cheap --batch attach probe (keeps the echoed-command regression
        # test honest) before we commit to a long-lived subprocess.
        probe = _run_lldb(self._pid,
            [
                self._lldb, "--batch", "-p", str(self._pid),
                "-o",
                "script import lldb; print('ATTACH_OK' if lldb.process.IsValid() else 'ATTACH_FAIL')",
                "-o", "detach",
                "-o", "quit",
            ],
            capture_output=True, text=True, timeout=_LLDB_TIMEOUT_S,
        )
        if "ATTACH_OK" not in (probe.stdout or "").splitlines():
            raise MemoryEngineUnavailable(
                f"lldb cannot attach to pid={self._pid}: "
                f"stdout={(probe.stdout or '')[:160]!r} stderr={(probe.stderr or '')[:160]!r}"
            )
        self._session = _PersistentLldbSession(self._pid, self._lldb)

    @property
    def session(self) -> _PersistentLldbSession:
        return self._session

    def read(self, address: int, size: int) -> bytes:
        return self._session.read_bytes(address, size)

    def write(self, address: int, data: bytes) -> None:
        self._session.write_bytes(address, data)

    def compare_write(self, address: int, expected: bytes, data: bytes) -> None:
        self._session.compare_write(address, expected, data)

    def close(self) -> None:
        session = getattr(self, "_session", None)
        if session is not None:
            session.close()


def _make_backend(pid: int) -> _MachReaderWriter | _LldbReaderWriter:
    if os.environ.get("BUGHUNTER_FORCE_LLDB") == "1":
        return _LldbReaderWriter(pid)
    try:
        return _MachReaderWriter(pid)
    except MemoryEngineUnavailable:
        return _LldbReaderWriter(pid)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


_SCALAR_FMT = {
    # Legacy aliases the panel + tests have been using.
    "int":    ("<i", 4),
    "uint":   ("<I", 4),
    "int16":  ("<h", 2),
    "int64":  ("<q", 8),
    "uint64": ("<Q", 8),
    "float":  ("<f", 4),
    "double": ("<d", 8),
    "byte":   ("<b", 1),
    "ubyte":  ("<B", 1),
    # Shared-contract wire names (contracts.MappingRecipe.value_type).
    "i8":  ("<b", 1), "i16": ("<h", 2), "i32": ("<i", 4), "i64": ("<q", 8),
    "u8":  ("<B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "u64": ("<Q", 8),
    "f32": ("<f", 4), "f64": ("<d", 8),
}
_FLOAT_TYPES = frozenset({"float", "double", "f32", "f64"})


@dataclass
class MacAdapterInfo:
    pid: int
    backend: str  # "mach" or "lldb"
    identity: ProcessIdentity
    persistent_lldb: bool = False
    lldb_session_pid: int | None = None
    last_op_latency_ms: float = 0.0


class MacMemoryAdapter:
    """Thin, reusable mach-vm + lldb adapter.

    Legacy :class:`bughunter.mac_memory.MacMemory` delegates to this.
    """

    def __init__(self, pid: int, *, identity: ProcessIdentity | None = None) -> None:
        self._pid = int(pid)
        birth = read_process_start_time_ns(self._pid)
        if birth <= 0:
            raise MemoryEngineUnavailable("process birth identity unavailable")
        if identity is not None and (identity.pid != self._pid or identity.start_time_ns != birth):
            raise MemoryEngineError("process birth identity mismatch")
        self._closed = False
        self._lease = ProcessLease(self._pid, birth)
        try:
            self._backend = _make_backend(self._pid)
        except BaseException:
            self._lease.close()
            raise
        self._backend_name = "mach" if isinstance(self._backend, _MachReaderWriter) else "lldb"
        if identity is None:
            identity = ProcessIdentity(
                pid=self._pid,
                start_time_ns=birth,
                target_fingerprint="",
            )
        self._identity = identity

    def _ensure_live(self):
        if self._closed or read_process_start_time_ns(self._pid) != self._identity.start_time_ns:
            self.close()
            raise MemoryEngineError("target closed, exited or process birth identity changed")

    @property
    def info(self) -> MacAdapterInfo:
        persistent = isinstance(self._backend, _LldbReaderWriter)
        session_pid: int | None = None
        last_latency: float = 0.0
        if persistent:
            session = getattr(self._backend, "session", None)
            if session is not None:
                session_pid = session.subprocess_pid
                last_latency = session.last_op_latency_ms
        return MacAdapterInfo(
            pid=self._pid,
            backend=self._backend_name,
            identity=self._identity,
            persistent_lldb=persistent,
            lldb_session_pid=session_pid,
            last_op_latency_ms=last_latency,
        )

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
        return self._backend.read(int(address), int(size))

    def write_bytes(self, address: int, data: bytes, *, verify: bool = True) -> None:
        self._ensure_live()
        if not 0 <= int(address) < 2**64:
            raise MemoryEngineError("address outside unsigned 64-bit range")
        if len(data) > 4 * 1024 * 1024:
            raise MemoryEngineError("write size exceeds the 4 MiB operation limit")
        # The persistent lldb backend bundles write+readback into one
        # round trip so we do not pay a second interrupt/continue for
        # the verify step.
        if verify and isinstance(self._backend, _LldbReaderWriter):
            self._backend.session.write_bytes(int(address), bytes(data), verify=True)
            return
        self._backend.write(int(address), bytes(data))
        if verify:
            readback = self._backend.read(int(address), len(data))
            if readback != data:
                raise MemoryEngineError(
                    f"write at {address:#x} did not verify: wrote {data!r} readback {readback!r}"
                )

    def read_value(self, address: int, vtype: str = "int") -> Any:
        fmt, size = _SCALAR_FMT[vtype]
        return struct.unpack(fmt, self.read_bytes(address, size))[0]

    def write_value(self, address: int, value: Any, vtype: str = "int", *, verify: bool = True) -> None:
        fmt, _ = _SCALAR_FMT[vtype]
        if vtype in _FLOAT_TYPES:
            value = float(value)
        else:
            value = int(value)
        self.write_bytes(address, struct.pack(fmt, value), verify=verify)

    def compare_write_value(self, address: int, expected: Any, value: Any, vtype: str) -> None:
        self._ensure_live()
        if not 0 < address < 2**64:
            raise MemoryEngineError('address outside unsigned 64-bit range')
        fmt, size = _SCALAR_FMT[vtype]
        # Normalise ints for integer vtypes so an operator passing the literal
        # ``60`` (Python int) does not fail ``struct.pack`` on an "f32" slot
        # or vice-versa. Floats pass through unchanged.
        if vtype in _FLOAT_TYPES:
            expected, value = float(expected), float(value)
        else:
            expected, value = int(expected), int(value)
        before, after = struct.pack(fmt, expected), struct.pack(fmt, value)
        if isinstance(self._backend, _LldbReaderWriter):
            self._backend.compare_write(address, before, after)
        else:
            if self.read_bytes(address, size) != before:
                raise MemoryEngineError('precondition_failed: value changed since read receipt')
            self.write_bytes(address, after, verify=True)

    @contextmanager
    def transaction(self):
        self._ensure_live()
        if isinstance(self._backend, _LldbReaderWriter):
            with self._backend.session.transaction():
                yield
        else:
            # Mach compare_write still refuses a changed expected value.
            yield

    def regions_writable(self) -> list[tuple[int, int]]:
        """Alias for ``regions(writable_only=True)`` so the P05 scan
        engine and the P06 LocalPlayer sweep can share the shared
        contract shape (``list[(base, size)]``)."""
        return self.regions(writable_only=True)

    def resolve_chain(self, base: int, offsets: list[int], *, vtype: str = "int") -> Any:
        """Walk a pointer chain exactly like Cheat Engine.

        For each but the last offset: ``addr = *(addr + off)``. The last
        offset is a plain add, then we read the typed value.
        """
        addr = int(base)
        for off in offsets[:-1]:
            raw = self.read_bytes(addr + int(off), 8)
            addr = struct.unpack("<Q", raw)[0]
        final = addr + (int(offsets[-1]) if offsets else 0)
        return {"final_address": final, "value": self.read_value(final, vtype)}

    def _inspect(self, expression: str):
        self._ensure_live()
        # When the lldb backend is a persistent session we must reuse
        # its attach — spawning a second ``lldb -p <pid>`` would fail
        # because the target is already being debugged. Dispatch the
        # script through the live session.
        if isinstance(self._backend, _LldbReaderWriter):
            session = getattr(self._backend, "_session", None)
            if session is not None:
                return session.inspect(expression)
        lldb = shutil.which("lldb")
        if not lldb:
            raise MemoryEngineUnavailable("lldb not on PATH for memory map inspection")
        script = "script import lldb,json; print('BH_MAP:'+json.dumps(" + expression + "))"
        result = _run_lldb(self._pid, [lldb, "--batch", "-p", str(self._pid), "-o", script,
                                 "-o", "detach", "-o", "quit"],
                                capture_output=True, text=True, timeout=_LLDB_TIMEOUT_S)
        for line in result.stdout.splitlines():
            if line.startswith("BH_MAP:"):
                return json.loads(line[7:])
        raise MemoryEngineError("lldb memory map inspection failed: " + result.stdout[-500:])

    def regions(self, writable_only: bool = False):
        # SBMemoryRegionInfoList supports iteration through its Python bindings.
        expression = "[(r.GetRegionBase(), r.GetRegionEnd()-r.GetRegionBase()) for r in lldb.process.GetMemoryRegions() if r.IsReadable()" + (" and r.IsWritable()" if writable_only else "") + "]"
        return [tuple(row) for row in self._inspect(expression)]

    def modules(self):
        return self._inspect("[{'path':str(m.GetFileSpec()), 'base':hex(m.GetObjectFileHeaderAddress().GetLoadAddress(lldb.target)), 'size':max([s.GetLoadAddress(lldb.target)+s.GetByteSize() for s in m.sections if s.GetLoadAddress(lldb.target) != lldb.LLDB_INVALID_ADDRESS]+[m.GetObjectFileHeaderAddress().GetLoadAddress(lldb.target)])-m.GetObjectFileHeaderAddress().GetLoadAddress(lldb.target)} for m in lldb.target.modules]")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._backend.close()
        finally:
            self._lease.close()

    def __enter__(self) -> "MacMemoryAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = ["MacMemoryAdapter", "MacAdapterInfo"]
