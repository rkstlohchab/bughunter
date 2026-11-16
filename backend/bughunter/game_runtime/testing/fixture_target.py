"""Parent-side helper for the disposable fixture target.

The :class:`Fixture` class is a context manager that spawns
``python -m bughunter.game_runtime.testing.fixture_process`` and
communicates with it over line-delimited JSON RPC on stdin/stdout. It is
the only sanctioned way for tests and harnesses to drive the fixture;
the independent :class:`~bughunter.game_runtime.testing.oracle.Oracle`
never uses it to answer value questions.
"""
from __future__ import annotations

import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


class FixtureError(RuntimeError):
    """Raised when the fixture subprocess misbehaves or exits unexpectedly."""


@dataclass
class RegionInfo:
    name: str
    base: int
    size: int

    @classmethod
    def from_wire(cls, entry: dict[str, Any]) -> "RegionInfo":
        base_s = entry["base"]
        if not isinstance(base_s, str) or not base_s.startswith("0x"):
            raise FixtureError(f"malformed region base: {base_s!r}")
        return cls(name=entry["name"], base=int(base_s, 16), size=int(entry["size"]))


@dataclass
class IdentityResponse:
    pid: int
    start_time_ns: int
    module_fingerprint: str
    region_map: list[RegionInfo] = field(default_factory=list)
    layout: str = "DEFAULT"

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "IdentityResponse":
        return cls(
            pid=int(data["pid"]),
            start_time_ns=int(data["start_time_ns"]),
            module_fingerprint=str(data["module_fingerprint"]),
            region_map=[RegionInfo.from_wire(r) for r in data.get("region_map", [])],
            layout=str(data.get("layout", "DEFAULT")),
        )

    def region(self, name: str) -> RegionInfo:
        for r in self.region_map:
            if r.name == name:
                return r
        raise KeyError(f"unknown region: {name!r}")


class Fixture:
    """Context-managed wrapper around the fixture subprocess."""

    def __init__(
        self,
        *,
        python: str | None = None,
        control_lie: dict[str, int] | None = None,
        regions: int | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._python = python or sys.executable
        self._control_lie = control_lie or {}
        self._regions = regions
        self._extra_env = extra_env or {}
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stdout_thread: threading.Thread | None = None
        self._responses: queue.Queue[str | None] = queue.Queue(maxsize=16)
        self._rpc_lock = threading.Lock()
        self._rpc_failed = False
        self._stderr_buf: list[str] = []
        self._closed = False
        self._drain_stop = threading.Event()
        # Cache of the identity response. Captured on the first
        # ``identity()`` call (while the fixture is running) so later
        # callers can read it even when another process (lldb) has
        # SIGSTOP'd the fixture via ptrace. Invalidated by ``move()``.
        self._identity_cache: "IdentityResponse | None" = None

    # ---------------- context manager ----------------

    def __enter__(self) -> "Fixture":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        if self._proc is not None:
            raise FixtureError("fixture already started")
        argv = [self._python, "-m", "bughunter.game_runtime.testing.fixture_process", "--layout", "DEFAULT"]
        if self._control_lie:
            spec = ",".join(f"{k}:{int(v)}" for k, v in self._control_lie.items())
            argv += ["--control-lie", spec]
        if self._regions is not None:
            argv += ["--regions", str(int(self._regions))]
        env = os.environ.copy()
        env.update(self._extra_env)
        # Make sure the child sees ``bughunter`` on its import path even
        # when the test runner has not installed it.
        backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        env["PYTHONPATH"] = backend_root + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
        self._stdout_thread.start()

    def _read_lines(self, stream):
        if os.name != 'posix':
            yield from stream
            return
        pending = b''
        while not self._drain_stop.is_set():
            if not select.select([stream.fileno()], [], [], 0.1)[0]:
                continue
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                return
            pending += chunk
            if len(pending) > 1024 * 1024:
                self._rpc_failed = True
                return
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                yield line.decode('utf-8', 'replace') + '\n'

    def _drain_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in self._read_lines(proc.stdout):
                try:
                    self._responses.put_nowait(line)
                except queue.Full:
                    self._rpc_failed = True
                    return
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._responses.put_nowait(None)
            except queue.Full:
                pass

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in self._read_lines(proc.stderr):
                self._stderr_buf.append(line.rstrip('\n'))
                self._stderr_buf[:] = self._stderr_buf[-100:]
        except (OSError, ValueError):
            pass

    def shutdown(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        deadline = time.monotonic() + max(0.0, timeout)
        pid = proc.pid

        def _alive() -> bool:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

        def _gone() -> bool:
            """True once the fixture has truly exited.

            ``proc.poll()`` returning not-None is ambiguous on darwin:
            a ptracer (lldb) takeover produces the same ECHILD that a
            real reap does, so Popen.poll() can read "exited" while
            the PID is still alive. Only trust it when ``os.kill(0)``
            also reports the PID as gone.
            """
            reaped = proc.poll() is not None
            return reaped and not _alive()

        def _await_gone(window: float) -> bool:
            end = time.monotonic() + max(0.0, window)
            while time.monotonic() < end:
                if _gone():
                    return True
                time.sleep(0.02)
            return _gone()

        try:
            # Persistent debugging resumes between operations, so use
            # cooperative shutdown even while a debugger owns wait events.
            if not self._rpc_failed and proc.stdin is not None and not proc.stdin.closed:
                try:
                    self._rpc({"method": "shutdown"}, timeout=max(0.0, deadline-time.monotonic()))
                except Exception:
                    pass
            # Wait for cooperative exit. Only escalate if the PID is
            # still ALIVE (not merely a zombie waiting for a reap).
            _await_gone(max(0.0, deadline-time.monotonic()))
            if _alive() and not _gone():
                # SIGCONT first in case a ptracer SIGSTOP'd the fixture;
                # then SIGTERM; then SIGKILL. os.kill reaches the target
                # regardless of waitpid parentage on darwin.
                try:
                    os.kill(pid, signal.SIGCONT)
                except OSError:
                    pass
                _await_gone(0.5)
            if _alive() and not _gone():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                _await_gone(0.5)
            if _alive() and not _gone():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                _await_gone(0.5)
        finally:
            self._drain_stop.set()
            # Join before closing buffered streams: a blocked readline owns
            # the buffer lock and closing it from another thread can deadlock.
            for thread in (self._stdout_thread, self._stderr_thread):
                if thread is not None:
                    thread.join(timeout=0.3)
            for stream, reader in ((proc.stdin, None), (proc.stdout, self._stdout_thread),
                                   (proc.stderr, self._stderr_thread)):
                if stream is not None and (reader is None or not reader.is_alive()):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

    # ---------------- introspection ----------------

    @property
    def pid(self) -> int:
        if self._proc is None:
            raise FixtureError("fixture not started")
        return self._proc.pid

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_buf)

    # ---------------- RPC ----------------

    def _rpc(self, payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._rpc_lock.acquire(timeout=max(0.0, timeout)):
            raise FixtureError("fixture RPC timed out waiting for the active request")
        try:
            return self._rpc_locked(payload, timeout=max(0.0, deadline-time.monotonic()))
        finally:
            self._rpc_lock.release()

    def _rpc_locked(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        proc = self._proc
        if proc is None:
            raise FixtureError("fixture not started")
        if self._rpc_failed:
            raise FixtureError("fixture RPC channel is unusable after timeout or protocol failure")
        # proc.poll() is unreliable here: on macOS, when another process
        # (lldb/debugserver) ptrace-attaches to the fixture, the
        # fixture's status is reported through the tracer rather than
        # through our waitpid(), and Popen.poll() can observe a
        # ``returncode=0`` while the fixture is in fact just SIGSTOP'd.
        # Use os.kill(pid, 0) which answers "does the PID still exist
        # in the OS" regardless of parent/tracer relationship.
        try:
            os.kill(proc.pid, 0)
        except OSError:
            # Really gone — process does not exist any more.
            raise FixtureError(
                f"fixture process {proc.pid} has exited "
                f"(poll={proc.poll()}, returncode={proc.returncode}); "
                f"stderr: {self._stderr_buf[-10:]}"
            )
        assert proc.stdin is not None and proc.stdout is not None
        encoded = json.dumps(payload) + "\n"
        if len(encoded.encode()) > 4096:
            raise FixtureError("fixture RPC request exceeds 4096 bytes")
        proc.stdin.write(encoded)
        proc.stdin.flush()
        try:
            line = self._responses.get(timeout=max(0.0, timeout))
        except queue.Empty as exc:
            self._rpc_failed = True
            raise FixtureError("fixture RPC response timed out; channel cannot be reused") from exc
        if not line:
            self._rpc_failed = True
            raise FixtureError(
                f"fixture closed stdout before responding; stderr: {self._stderr_buf[-10:]}"
            )
        try:
            response = json.loads(line)
        except Exception as exc:
            self._rpc_failed = True
            raise FixtureError(f"malformed fixture response: {line!r}: {exc}") from exc
        if "error" in response:
            raise FixtureError(f"fixture rejected RPC: {response['error']}")
        return response

    def identity(self) -> IdentityResponse:
        if self._identity_cache is None:
            self._identity_cache = IdentityResponse.from_wire(
                self._rpc({"method": "identity"})
            )
        return self._identity_cache

    def get(self, field: str) -> Any:
        return self._rpc({"method": "get", "field": field})["value"]

    def set(self, field: str, value: Any) -> None:
        self._rpc({"method": "set", "field": field, "value": value})

    def move(self, regions: Iterable[str]) -> list[RegionInfo]:
        # move() changes region bases, invalidating any cached identity.
        self._identity_cache = None
        payload = self._rpc({"method": "move", "regions": list(regions)})
        return [RegionInfo.from_wire(r) for r in payload["new_region_map"]]


__all__ = ["Fixture", "FixtureError", "IdentityResponse", "RegionInfo"]
