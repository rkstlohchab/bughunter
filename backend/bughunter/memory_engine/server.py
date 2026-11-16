"""Memory engine helper service.

Dispatches ``read``/``write``/``scan``/``pointer_search``/``tool_control``
requests against an underlying OS adapter. Every operation is bound to
process-birth identity (pid + start_time_ns + target fingerprint), not
pid alone, so a reused PID cannot accept a stale operation.

Tool control is a thin lane for launching and stopping disposable
helper processes (Cheat Engine, Ghidra, lldb, a scratch Python). It is
the same ``tool_control`` lane the plan references in P03. Readiness
is detected by a condition callback; stop goes through terminate →
wait → kill (POSIX ``killpg`` when available), with the postcondition
being the captured exit code.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .ownership import AttachBusy, ExecutorRegistry, OwnerKey, DEFAULT_OWNERSHIP
from .protocol import (
    MemoryEngineError,
    PartialReadError,
    ProcessIdentity,
    read_process_start_time_ns,
)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class MemoryAdapter(Protocol):
    @property
    def identity(self) -> ProcessIdentity: ...
    def read_bytes(self, address: int, size: int) -> bytes: ...
    def write_bytes(self, address: int, data: bytes, *, verify: bool = True) -> None: ...
    def read_value(self, address: int, vtype: str = "int") -> Any: ...
    def write_value(self, address: int, value: Any, vtype: str = "int", *, verify: bool = True) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ServiceConfig:
    machine_id: str = ""
    device_id: str = ""
    max_scan_bytes: int = 256 * 1024 * 1024  # 256 MiB resident scan budget
    max_scan_results: int = 500_000
    ownership: ExecutorRegistry = field(default_factory=lambda: DEFAULT_OWNERSHIP)


# ---------------------------------------------------------------------------
# Tool control
# ---------------------------------------------------------------------------


@dataclass
class ToolHandle:
    tool_id: str
    pid: int
    command: list[str]
    started_at: float
    ready: bool = False
    exit_code: int | None = None


class ToolController:
    """Launch, probe, and stop disposable tool processes.

    The controller does not model fully-managed long-lived helpers; it
    is the mechanism P03.tool_control tests use to demonstrate that a
    command we started also stops when told, and reports its exit code
    as postcondition evidence.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: dict[str, tuple[ToolHandle, subprocess.Popen[bytes]]] = {}

    def launch(
        self,
        command: list[str],
        *,
        ready_check: Callable[[subprocess.Popen[bytes]], bool] | None = None,
        ready_stdout: str | None = None,
        ready_timeout_s: float = 2.0,
        env: dict[str, str] | None = None,
    ) -> ToolHandle:
        popen_kwargs: dict[str, Any] = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **(env or {})},
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **popen_kwargs)  # noqa: S603 - trusted caller
        ready_event = threading.Event()
        def drain(stream, check_ready=False):
            pending = b""
            while True:
                block = stream.read1(4096)
                if not block:
                    break
                pending = (pending + block)[-65536:]
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if check_ready and ready_stdout is not None and line.decode("utf-8", "replace").strip() == ready_stdout:
                        ready_event.set()
        for stream, check in ((proc.stdout, True), (proc.stderr, False)):
            threading.Thread(target=drain, args=(stream, check), daemon=True).start()
        handle = ToolHandle(
            tool_id=uuid.uuid4().hex,
            pid=proc.pid,
            command=list(command),
            started_at=time.monotonic(),
        )
        if ready_check is not None or ready_stdout is not None:
            deadline = time.monotonic() + max(0.0, ready_timeout_s)
            while time.monotonic() < deadline:
                try:
                    if proc.poll() is not None:
                        break
                    if ready_event.is_set() or (ready_check is not None and ready_check(proc)):
                        handle.ready = True
                        break
                except Exception:
                    break
                time.sleep(0.02)
        with self._lock:
            self._tools[handle.tool_id] = (handle, proc)
        return handle

    def stop(self, tool_id: str, *, timeout_s: float = 2.0) -> ToolHandle:
        with self._lock:
            entry = self._tools.get(tool_id)
        if entry is None:
            raise KeyError(f"unknown tool_id: {tool_id!r}")
        handle, proc = entry
        # The session leader can exit while descendants still hold its pipes.
        # Its process group remains ours until those descendants exit too.
        if os.name == "posix":
            def signal_group(sig: int) -> bool:
                try:
                    os.killpg(proc.pid, sig)
                    return True
                except ProcessLookupError:
                    return False
                except PermissionError:
                    # Darwin can return EPERM for a group whose leader was
                    # just reaped. Confirm absence from the process table;
                    # a live/unknown member still retains ownership.
                    if proc.poll() is not None:
                        snapshot = subprocess.run(['ps', '-axo', 'pgid=,stat='],
                                                  capture_output=True, text=True, timeout=1)
                        if snapshot.returncode == 0:
                            members = [line.split()[1] for line in snapshot.stdout.splitlines()
                                       if len(line.split()) == 2 and line.split()[0] == str(proc.pid)]
                            if not any(not state.startswith('Z') for state in members):
                                return False
                    raise
            try:
                alive = signal_group(signal.SIGTERM)
                deadline = time.monotonic() + max(0.1, timeout_s)
                while alive and time.monotonic() < deadline:
                    proc.poll()  # Reap the leader before checking the group.
                    alive = signal_group(0)
                    if alive:
                        time.sleep(0.02)
                if alive:
                    signal_group(signal.SIGKILL)
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MemoryEngineError("tool termination was not confirmed; ownership retained") from exc
        elif proc.poll() is None:
            try:
                if os.name == "posix":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        proc.terminate()
                else:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=5, check=False)
            except Exception:
                pass
            deadline = time.monotonic() + max(0.1, timeout_s)
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.02)
            if proc.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        handle.exit_code = proc.returncode
        if handle.exit_code is None:
            raise MemoryEngineError("tool termination was not confirmed; ownership retained")
        handle.ready = False
        try:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
        except Exception:
            pass
        with self._lock:
            self._tools.pop(tool_id, None)
        return handle

    def get(self, tool_id: str) -> ToolHandle:
        with self._lock:
            entry = self._tools.get(tool_id)
        if entry is None:
            raise KeyError(f"unknown tool_id: {tool_id!r}")
        handle, proc = entry
        if proc.poll() is not None and handle.exit_code is None:
            handle.exit_code = proc.returncode
            handle.ready = False
        return handle

    def shutdown(self) -> None:
        with self._lock:
            ids = list(self._tools.keys())
        for tid in ids:
            try:
                self.stop(tid, timeout_s=1.0)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    session_id: str
    owner_id: str
    key: OwnerKey
    adapter: MemoryAdapter
    mutating: bool


class MemoryEngineServer:
    """Request dispatcher on top of a memory adapter.

    The server does not own the adapter's construction: callers hand it
    in through :meth:`attach` with a known identity. That keeps this
    class portable (no platform imports) and testable.
    """

    def __init__(self, config: ServiceConfig | None = None) -> None:
        self._config = config or ServiceConfig()
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        from .discovery import DiscoveryService
        self.discovery = DiscoveryService(self._config)
        self.tools = ToolController()

    @property
    def config(self) -> ServiceConfig:
        return self._config

    # Lifecycle ----------------------------------------------------------

    def attach(self, adapter: MemoryAdapter, *, session_id: str | None = None,
               mutating: bool = True) -> dict[str, Any]:
        with self._lock:
            return self._attach(adapter, session_id=session_id, mutating=mutating)

    def _attach(
        self,
        adapter: MemoryAdapter,
        *,
        session_id: str | None = None,
        mutating: bool = True,
    ) -> dict[str, Any]:
        """Register ``adapter`` under its process-birth identity."""
        identity = adapter.identity
        key = OwnerKey.of(
            pid=identity.pid,
            start_time_ns=identity.start_time_ns,
            machine_id=self._config.machine_id,
            device_id=self._config.device_id,
            target_fingerprint=identity.target_fingerprint,
        )
        session_id = session_id or uuid.uuid4().hex
        with self._lock:
            if session_id in self._sessions:
                raise MemoryEngineError("session already attached")
        try:
            owner_id = self._config.ownership.attach(key, session_id=session_id, mutating=mutating)
        except AttachBusy as busy:
            return {
                "status": "busy",
                "holder": {"owner_id": busy.holder_id, "session_id": busy.holder_session},
                "identity": identity.to_wire(),
            }
        session = _Session(session_id=session_id, owner_id=owner_id, key=key, adapter=adapter, mutating=mutating)
        with self._lock:
            existing = next((s for s in self._sessions.values() if s.key == key), None)
            if existing is not None:
                if adapter is not existing.adapter:
                    adapter.close()
                session.adapter = existing.adapter
            self._sessions[session_id] = session
        return {
            "status": "attached",
            "session_id": session_id,
            "owner_id": owner_id,
            "identity": identity.to_wire(),
            "mutating": mutating,
        }

    def detach(self, session_id: str) -> dict[str, Any]:
        self.cancel_work(session_id=session_id)
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return {"status": "unknown"}
            self.discovery.close_owner(session_id)
            if not any(s.adapter is session.adapter for s in self._sessions.values()):
                session.adapter.close()
            self._config.ownership.release(session.key, session.owner_id)
        return {"status": "detached", "session_id": session_id}

    def _session(self, session_id: str, identity: ProcessIdentity | None = None) -> _Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise MemoryEngineError(f"no session: {session_id!r}")
        if identity is None or not session.adapter.identity.matches(identity):
            raise MemoryEngineError(
                f"process identity mismatch: "
                f"session={session.adapter.identity.to_wire()} request={identity.to_wire() if identity else None}"
            )
        return session

    # Dispatch -----------------------------------------------------------

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a decoded RPC request. The server only handles the
        semantic layer; the transport (reader + writer + queue) is
        wrapped around it by the game bridge.
        """
        handler = _METHOD_TABLE.get(method)
        if handler is None:
            raise MemoryEngineError(f"unknown method: {method!r}")
        with self._lock:
            return handler(self, params)

    # --- read / write ---------------------------------------------------

    def _read(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params["session_id"], _identity_from(params))
        address = _address(params["address"])
        if "vtype" in params:
            value = session.adapter.read_value(address, params["vtype"])
            return {"address": f"0x{address:X}", "type": params["vtype"], "value": value}
        size = int(params["size"])
        try:
            raw = session.adapter.read_bytes(address, size)
        except PartialReadError as exc:
            return {
                "error": {
                    "code": "partial_read",
                    "address": f"0x{exc.address:X}",
                    "requested": exc.requested,
                    "got": exc.got,
                    "reason": exc.reason,
                }
            }
        return {"address": f"0x{address:X}", "size": size, "bytes": raw.hex()}

    def _write(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params["session_id"], _identity_from(params))
        if not session.mutating:
            raise MemoryEngineError("read-only session cannot write")
        address = _address(params["address"])
        verify = bool(params.get("verify", True))
        if "vtype" in params:
            if 'expected_value' in params:
                compare_write = getattr(session.adapter, 'compare_write_value', None)
                if compare_write:
                    compare_write(address, params['expected_value'], params['value'], params['vtype'])
                    verify = True
                else:
                    if session.adapter.read_value(address, params['vtype']) != params['expected_value']:
                        raise MemoryEngineError('precondition_failed: value changed since read receipt')
                    session.adapter.write_value(address, params['value'], params['vtype'], verify=verify)
            else:
                session.adapter.write_value(address, params["value"], params["vtype"], verify=verify)
            return {"address": f"0x{address:X}", "type": params["vtype"], "written": params["value"], "verified": verify}
        data = bytes.fromhex(str(params["bytes"]))
        session.adapter.write_bytes(address, data, verify=verify)
        return {"address": f"0x{address:X}", "size": len(data), "verified": verify}

    # --- bounded subscriber-owned discovery -----------------------------

    def _scan(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params["session_id"], _identity_from(params))
        return self.discovery.scan(session.adapter, params)

    def _pointer_search(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params["session_id"], _identity_from(params))
        return self.discovery.pointer_search(session.adapter, params)

    def cancel_work(self, session_id=None, task_session_id=None, scan_id=None):
        """Signal discovery without waiting for the dispatch/service lock."""
        return self.discovery.cancel_work(session_id=session_id,
                    task_session_id=task_session_id, scan_id=scan_id)

    # --- tool_control ---------------------------------------------------

    def _tool_control(self, params: dict[str, Any]) -> dict[str, Any]:
        action = str(params.get("action", "")).lower()
        if action == "launch":
            command = list(params["command"])
            ready_timeout = float(params.get("ready_timeout_s", 2.0))
            handle = self.tools.launch(command, ready_timeout_s=ready_timeout, ready_stdout=params.get("ready_stdout"))
            return {
                "tool_id": handle.tool_id,
                "pid": handle.pid,
                "ready": handle.ready,
                "command": handle.command,
            }
        if action == "status":
            handle = self.tools.get(params["tool_id"])
            return {
                "tool_id": handle.tool_id,
                "pid": handle.pid,
                "ready": handle.ready,
                "exit_code": handle.exit_code,
            }
        if action == "stop":
            handle = self.tools.stop(params["tool_id"], timeout_s=float(params.get("timeout_s", 2.0)))
            return {
                "tool_id": handle.tool_id,
                "pid": handle.pid,
                "exit_code": handle.exit_code,
                "ready": handle.ready,
            }
        raise MemoryEngineError(f"unknown tool_control action: {action!r}")

    # --- shutdown -------------------------------------------------------

    def shutdown(self) -> None:
        with self._lock:
            session_ids = list(self._sessions.keys())
        for sid in session_ids:
            try:
                self.detach(sid)
            except Exception:
                pass
        self.tools.shutdown()


def _identity_from(params: dict[str, Any]) -> ProcessIdentity | None:
    ident = params.get("identity")
    if not ident:
        return None
    return ProcessIdentity.from_wire(ident)


def _address(value):
    if not isinstance(value, str):
        raise MemoryEngineError("wire addresses must be decimal or 0x-prefixed strings")
    address = int(value, 16 if value.startswith("0x") else 10)
    if not 0 <= address < 2**64:
        raise MemoryEngineError("address outside uint64 range")
    return address


_METHOD_TABLE: dict[str, Callable[["MemoryEngineServer", dict[str, Any]], dict[str, Any]]] = {
    "memory.read": MemoryEngineServer._read,
    "memory.write": MemoryEngineServer._write,
    "memory.scan": MemoryEngineServer._scan,
    "memory.pointer_search": MemoryEngineServer._pointer_search,
    "tool_control": MemoryEngineServer._tool_control,
}


__all__ = [
    "MemoryAdapter",
    "MemoryEngineServer",
    "ServiceConfig",
    "ToolController",
    "ToolHandle",
]
