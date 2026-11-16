"""Wire protocol + transport primitives for the native memory helper.

Three invariants hold for every connection:

* **One bounded writer queue** — producers do not block on a slow or
  stuck helper; they get back-pressure with a documented ceiling.
* **One permanent stdout reader** — a single thread reads every frame
  from the helper and routes it: RPC responses to the waiter keyed by
  ``id``, versioned notifications to the notification sink, the latest
  streamed frame to a capacity-one channel.
* **Prioritised control** — ``stop`` and other control messages jump the
  queue ahead of ordinary requests; latest-frame notifications evict
  older frames instead of blocking the writer.

Messages are line-delimited UTF-8 JSON. Default per-message ceiling is
4 MiB. Scan results that exceed that use pagination references
(``{"page": ..., "cursor": ...}``) and never embed the full payload.
"""
from __future__ import annotations

import heapq
import io
import itertools
import json
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default message size ceiling. Scan results that exceed this use pages.
DEFAULT_MAX_MESSAGE_SIZE: int = 4 * 1024 * 1024

#: The latest-frame channel holds exactly one frame; new frames evict the
#: previous one so the consumer always observes the freshest value.
LATEST_FRAME_CAPACITY: int = 1

#: Priority ordering for the writer queue. Lower = sent sooner.
PRIORITY_STOP: int = 0
PRIORITY_CONTROL: int = 1
PRIORITY_NORMAL: int = 2

#: Current notification schema version. Every emitted notification carries
#: this so clients can refuse incompatible helpers instead of guessing.
NOTIFICATION_VERSION: int = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MemoryEngineError(Exception):
    """Base class for every memory-engine-raised error."""


class MemoryEngineUnavailable(MemoryEngineError):
    """Adapter cannot be constructed on this platform or in this process."""


class MemoryEngineProtocolError(MemoryEngineError):
    """A frame violated the wire format (too big, malformed JSON, ...)."""


@dataclass
class PartialReadError(MemoryEngineError):
    """A read returned fewer bytes than requested.

    Structured so callers can distinguish 'page not mapped' from 'page
    straddled a boundary' from 'target died mid-read'.
    """

    address: int
    requested: int
    got: int
    reason: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"partial read at {self.address:#x}: "
            f"got {self.got}/{self.requested} bytes ({self.reason})"
        )


# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessIdentity:
    """Birth identity of a target process.

    A bare PID can be reused after the process exits. Every operation
    carries ``pid + start_time_ns + target_fingerprint`` so the engine
    rejects a stale handle instead of poking an unrelated process that
    happened to inherit the number.
    """

    pid: int
    start_time_ns: int
    target_fingerprint: str = ""

    def matches(self, other: "ProcessIdentity") -> bool:
        if self.pid != other.pid:
            return False
        if self.start_time_ns != other.start_time_ns:
            return False
        if self.target_fingerprint and other.target_fingerprint:
            return self.target_fingerprint == other.target_fingerprint
        return True

    def to_wire(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "start_time_ns": self.start_time_ns,
            "target_fingerprint": self.target_fingerprint,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "ProcessIdentity":
        return cls(
            pid=int(data["pid"]),
            start_time_ns=int(data["start_time_ns"]),
            target_fingerprint=str(data.get("target_fingerprint", "")),
        )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass
class Request:
    method: str
    id: int
    params: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {"kind": "request", "id": self.id, "method": self.method, "params": self.params}

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "Request":
        return cls(method=str(data["method"]), id=int(data["id"]), params=dict(data.get("params") or {}))


@dataclass
class Response:
    id: int
    result: Any = None
    error: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": "response", "id": self.id}
        if self.error is not None:
            body["error"] = self.error
        else:
            body["result"] = self.result
        return body

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "Response":
        return cls(
            id=int(data["id"]),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class Notification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    version: int = NOTIFICATION_VERSION

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": "notification",
            "method": self.method,
            "version": self.version,
            "params": self.params,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "Notification":
        return cls(
            method=str(data["method"]),
            params=dict(data.get("params") or {}),
            version=int(data.get("version", NOTIFICATION_VERSION)),
        )


# ---------------------------------------------------------------------------
# Frame encoding
# ---------------------------------------------------------------------------


def encode_frame(message: Request | Response | Notification, *, max_size: int = DEFAULT_MAX_MESSAGE_SIZE) -> bytes:
    """Serialize ``message`` to a single line of UTF-8 JSON + ``\\n``.

    Raises :class:`MemoryEngineProtocolError` when the encoded frame
    exceeds ``max_size``. Callers that need to return a huge scan result
    should paginate: ``{"page": N, "cursor": "..."}`` with a follow-up
    ``memory.scan.page`` request.
    """
    wire = message.to_wire()
    try:
        encoded = (json.dumps(wire, separators=(",", ":"), default=_json_default) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MemoryEngineProtocolError(f"cannot encode frame: {exc}") from exc
    if len(encoded) > max_size:
        raise MemoryEngineProtocolError(
            f"frame exceeds {max_size} bytes ({len(encoded)}); use pagination reference"
        )
    return encoded


def decode_frame(line: str | bytes) -> Request | Response | Notification:
    """Parse a single UTF-8 JSON line into a typed message.

    Malformed JSON, unknown ``kind``, and missing required fields all
    raise :class:`MemoryEngineProtocolError`.
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryEngineProtocolError(f"frame is not utf-8: {exc}") from exc
    line = line.strip()
    if not line:
        raise MemoryEngineProtocolError("empty frame")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MemoryEngineProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryEngineProtocolError("frame is not a JSON object")
    kind = data.get("kind")
    try:
        if kind == "request":
            return Request.from_wire(data)
        if kind == "response":
            return Response.from_wire(data)
        if kind == "notification":
            return Notification.from_wire(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise MemoryEngineProtocolError(f"missing fields on {kind!r} frame: {exc}") from exc
    raise MemoryEngineProtocolError(f"unknown frame kind: {kind!r}")


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, ProcessIdentity):
        return value.to_wire()
    raise TypeError(f"not serialisable: {type(value).__name__}")


# ---------------------------------------------------------------------------
# Writer queue
# ---------------------------------------------------------------------------


class BoundedWriterQueue:
    """Priority queue in front of the helper's stdin.

    * A single writer thread drains the queue; producers only enqueue.
    * ``stop`` and other control messages take priority over ordinary
      requests so a cancel reaches the helper before the backlog.
    * ``capacity`` bounds pending work. When full, ``put`` raises
      ``MemoryEngineProtocolError`` so the caller sees back-pressure
      instead of silently dropping.
    """

    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._heap: list[tuple[int, int, bytes]] = []
        self._counter = itertools.count()
        self._closed = False

    def put(self, payload: bytes, *, priority: int = PRIORITY_NORMAL) -> None:
        with self._not_empty:
            if self._closed:
                raise MemoryEngineProtocolError("writer queue is closed")
            if len(self._heap) >= self._capacity and priority >= PRIORITY_NORMAL:
                raise MemoryEngineProtocolError(
                    f"writer queue is full ({self._capacity}); helper is not keeping up"
                )
            if len(self._heap) >= self._capacity + 8:
                raise MemoryEngineProtocolError("reserved control queue is full")
            heapq.heappush(self._heap, (int(priority), next(self._counter), payload))
            self._not_empty.notify()

    def get(self, timeout: float | None = None) -> bytes | None:
        with self._not_empty:
            deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
            while not self._heap and not self._closed:
                if deadline is None:
                    self._not_empty.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._not_empty.wait(timeout=remaining)
            if not self._heap:
                return None
            _, _, payload = heapq.heappop(self._heap)
            return payload

    def close(self) -> None:
        with self._not_empty:
            self._closed = True
            self._not_empty.notify_all()

    def qsize(self) -> int:
        with self._lock:
            return len(self._heap)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed


# ---------------------------------------------------------------------------
# Latest-frame channel
# ---------------------------------------------------------------------------


class LatestFrameChannel:
    """Capacity-one channel for live frame streaming.

    The producer always overwrites the previous entry; the consumer
    always sees the most recent frame. Dropped older frames are counted
    so staleness can be surfaced.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._value: dict[str, Any] | None = None
        self._dropped = 0
        self._sequence = 0

    def publish(self, frame: dict[str, Any]) -> None:
        with self._cond:
            if self._value is not None:
                self._dropped += 1
            self._value = frame
            self._sequence += 1
            self._cond.notify_all()

    def consume(self, timeout: float | None = None) -> dict[str, Any] | None:
        with self._cond:
            if self._value is None:
                if timeout is None:
                    return None
                self._cond.wait(timeout=timeout)
            value, self._value = self._value, None
            return value

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def clear(self) -> None:
        with self._lock:
            self._value = None


# ---------------------------------------------------------------------------
# Permanent reader loop
# ---------------------------------------------------------------------------


class PermanentReaderLoop:
    """Single thread that reads every line the helper emits.

    Routing:

    * ``response`` → the waiter keyed by ``id`` (then removed).
    * ``notification`` with method ``frame.*`` → the latest-frame channel.
    * other notifications → the general notification sink.
    * malformed frames → the on_malformed callback (default: counted).

    The loop runs until the stream is exhausted or :meth:`stop` is
    called; on exit every pending waiter receives
    :class:`MemoryEngineProtocolError` so nothing hangs.
    """

    def __init__(
        self,
        stream: io.IOBase,
        *,
        on_notification: Callable[[Notification], None] | None = None,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        on_malformed: Callable[[str, Exception], None] | None = None,
        max_message_size: int = DEFAULT_MAX_MESSAGE_SIZE,
    ) -> None:
        self._stream = stream
        self._on_notification = on_notification
        self._on_frame = on_frame
        self._on_malformed = on_malformed
        self._max = max_message_size
        self._lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, Response] = {}
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self.malformed_count = 0
        self.notifications_seen = 0
        self.frames_seen = 0
        self.responses_seen = 0

    # Lifetime -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="memory-engine-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        try:
            self._stream.close()
        except Exception:
            pass
        with self._lock:
            waiters = list(self._pending.items())
            self._pending.clear()
        for ident, event in waiters:
            self._results[ident] = Response(id=ident, error={"code": "transport_closed", "message": "reader stopped"})
            event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    # Waiter registration -----------------------------------------------

    def expect(self, request_id: int) -> threading.Event:
        event = threading.Event()
        with self._lock:
            # If the response (or a transport-closed stub) already
            # arrived, fire the event right away so a late caller does
            # not hang.
            if request_id in self._results:
                event.set()
            elif self._stopped.is_set():
                # Transport is already gone — synthesise a closed-stub
                # result so a late waiter does not block forever.
                self._results[request_id] = Response(
                    id=request_id,
                    error={"code": "transport_closed", "message": "reader already stopped"},
                )
                event.set()
            self._pending[request_id] = event
        return event

    def take(self, request_id: int) -> Response:
        with self._lock:
            response = self._results.pop(request_id, None)
            self._pending.pop(request_id, None)
        if response is None:
            raise MemoryEngineProtocolError(f"no response recorded for id={request_id}")
        return response

    def await_response(self, request_id: int, *, timeout: float) -> Response:
        event = self.expect(request_id)
        if not event.wait(timeout=timeout):
            with self._lock:
                self._pending.pop(request_id, None)
                self._results.pop(request_id, None)
            raise MemoryEngineProtocolError(f"timeout waiting for response id={request_id}")
        return self.take(request_id)

    # Internal loop ------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    raw = self._stream.readline(self._max + 1)
                except (ValueError, OSError):
                    break
                if raw is None or raw == b"" or raw == "":
                    break
                if isinstance(raw, bytes):
                    line = raw.decode("utf-8", errors="replace")
                else:
                    line = raw
                line = line.rstrip("\n").rstrip("\r")
                if not line.strip():
                    continue
                if len(line.encode("utf-8")) > self._max:
                    err = MemoryEngineProtocolError(
                        f"inbound frame exceeds {self._max} bytes"
                    )
                    self._handle_malformed(line, err)
                    break
                try:
                    message = decode_frame(line)
                except MemoryEngineProtocolError as exc:
                    self._handle_malformed(line, exc)
                    continue
                self._route(message)
        finally:
            self._stopped.set()
            self._drain_pending("helper closed stdout")

    def _route(self, message: Request | Response | Notification) -> None:
        if isinstance(message, Response):
            self.responses_seen += 1
            with self._lock:
                event = self._pending.get(message.id)
                self._results[message.id] = message
            if event is not None:
                event.set()
            return
        if isinstance(message, Notification):
            self.notifications_seen += 1
            if message.method.startswith("frame.") and self._on_frame is not None:
                self.frames_seen += 1
                try:
                    self._on_frame(message.params)
                except Exception:
                    pass
                return
            if self._on_notification is not None:
                try:
                    self._on_notification(message)
                except Exception:
                    pass
            return
        # Server-side helpers do not receive requests from clients on
        # this channel, so an inbound request is unexpected but not
        # fatal; surface it as a malformed frame.
        self._handle_malformed(repr(message), MemoryEngineProtocolError("unexpected inbound request"))

    def _handle_malformed(self, raw: str, exc: Exception) -> None:
        self.malformed_count += 1
        try:
            data = json.loads(raw)
            ident = data.get("id") if isinstance(data, dict) else None
        except (ValueError, TypeError):
            ident = None
        with self._lock:
            pending = ([ident] if ident in self._pending else list(self._pending))
            for request_id in pending:
                if request_id not in self._results:
                    self._results[request_id] = Response(id=request_id, error={"code": "malformed_frame", "message": str(exc)})
                    self._pending[request_id].set()
        if self._on_malformed is not None:
            try:
                self._on_malformed(raw, exc)
            except Exception:
                pass

    def _drain_pending(self, reason: str) -> None:
        with self._lock:
            waiters = list(self._pending.items())
            self._pending.clear()
        for ident, event in waiters:
            self._results[ident] = Response(id=ident, error={"code": "transport_closed", "message": reason})
            event.set()


# ---------------------------------------------------------------------------
# Pid birth-time helpers (used by adapters)
# ---------------------------------------------------------------------------


def read_process_start_time_ns(pid: int) -> int:
    """Return a process' start time in nanoseconds since epoch.

    Zero means the host does not expose it. Callers must then treat
    identity as pid-only and not promise birth-identity guarantees.
    """
    try:
        import psutil  # type: ignore

        process = psutil.Process(pid)
        if process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return 0
        return int(process.create_time() * 1_000_000_000)
    except Exception:
        pass
    if sys.platform == "darwin":
        import ctypes
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            lib.proc_pidinfo.restype = ctypes.c_int
            # PROC_PIDTBSDINFO / proc_bsdinfo from libproc.h (136 bytes).
            buf = ctypes.create_string_buffer(136)
            if lib.proc_pidinfo(int(pid), 3, 0, buf, 136) == 136:
                # proc_bsdinfo.pbi_status, sys/proc.h SZOMB. A zombie
                # retains its birth timestamp but is no longer a target.
                if struct.unpack_from('=I', buf.raw, 4)[0] == 5:
                    return 0
                sec, usec = struct.unpack_from("=QQ", buf.raw, 120)
                return sec * 1_000_000_000 + usec * 1000
        except (OSError, ValueError):
            return 0
    # Platform fall-backs below: proc on Linux, kinfo_proc on macOS,
    # none on Windows (OpenProcess+GetProcessTimes requires a handle).
    if hasattr(os, "sysconf"):
        try:
            with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as fh:
                record = fh.read()
                # comm is parenthesized and may contain spaces or ')'.
                parts = record[record.rfind(')') + 2:].split()
            if parts[0] in {'Z', 'X'}:
                return 0
            # Field 22 (index 21) is starttime in clock ticks since boot.
            ticks_per_sec = os.sysconf("SC_CLK_TCK")
            starttime_ticks = int(parts[19])
            with open("/proc/stat", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("btime "):
                        btime = int(line.split()[1])
                        break
                else:
                    btime = 0
            if btime:
                return (btime + starttime_ticks // ticks_per_sec) * 1_000_000_000 + (
                    (starttime_ticks % ticks_per_sec) * (1_000_000_000 // ticks_per_sec)
                )
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
            return 0
    return 0


__all__ = [
    "BoundedWriterQueue",
    "DEFAULT_MAX_MESSAGE_SIZE",
    "LATEST_FRAME_CAPACITY",
    "LatestFrameChannel",
    "MemoryEngineError",
    "MemoryEngineProtocolError",
    "MemoryEngineUnavailable",
    "NOTIFICATION_VERSION",
    "Notification",
    "PRIORITY_CONTROL",
    "PRIORITY_NORMAL",
    "PRIORITY_STOP",
    "PartialReadError",
    "PermanentReaderLoop",
    "ProcessIdentity",
    "Request",
    "Response",
    "decode_frame",
    "encode_frame",
    "read_process_start_time_ns",
]
