"""Executor ownership registry.

The rule: for any given target process there is at most one mutating
executor at a time. Read-only subscribers can share the owner. A second
mutating attach gets ``AttachBusy`` with the current owner's session id
so the caller can report it instead of silently taking over.

Keys are keyed by (machine_id, device_id, pid, start_time_ns,
target_fingerprint) rather than pid alone — a reused PID on the same
host would otherwise be indistinguishable.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class OwnerKey:
    machine_id: str
    device_id: str
    pid: int
    start_time_ns: int
    target_fingerprint: str = field(compare=False)

    @classmethod
    def of(
        cls,
        pid: int,
        start_time_ns: int,
        *,
        machine_id: str = "",
        device_id: str = "",
        target_fingerprint: str = "",
    ) -> "OwnerKey":
        return cls(
            machine_id=machine_id,
            device_id=device_id,
            pid=int(pid),
            start_time_ns=int(start_time_ns),
            target_fingerprint=target_fingerprint,
        )


class AttachBusy(RuntimeError):
    """A second mutating attach was attempted while one is already live."""

    def __init__(self, key: OwnerKey, holder_id: str, holder_session: str) -> None:
        super().__init__(
            f"target {key.pid}:{key.start_time_ns} already held by {holder_id} ({holder_session})"
        )
        self.key = key
        self.holder_id = holder_id
        self.holder_session = holder_session


@dataclass
class _OwnerSlot:
    owner_id: str
    session_id: str
    subscribers: set[str] = field(default_factory=set)
    mutating: bool = True


class ExecutorRegistry:
    """One mutating owner per target process; many read-only subscribers.

    This is the Python mirror of the Rust side's executor table — the
    native-helper layer also holds an executor-epoch/credential pair for
    every slot; this class owns the logical acquire/release contract
    only, so it stays importable and testable without native IO.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[OwnerKey, _OwnerSlot] = {}

    def attach(
        self,
        key: OwnerKey,
        *,
        session_id: str,
        mutating: bool = True,
    ) -> str:
        """Register an executor. Returns an opaque owner id.

        * ``mutating=True`` fails with :class:`AttachBusy` when another
          mutating owner is already present.
        * ``mutating=False`` always succeeds and attaches as a shared
          read-only subscriber; a mutating owner may still be present.
        """
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                owner_id = uuid.uuid4().hex
                self._slots[key] = _OwnerSlot(
                    owner_id=owner_id,
                    session_id=session_id,
                    subscribers={owner_id},
                    mutating=mutating,
                )
                return owner_id
            if mutating and slot.mutating:
                raise AttachBusy(key, slot.owner_id, slot.session_id)
            if mutating and not slot.mutating:
                # Promote this new attach to the mutating owner.
                slot.mutating = True
                slot.owner_id = uuid.uuid4().hex
                slot.session_id = session_id
                slot.subscribers.add(slot.owner_id)
                return slot.owner_id
            # Read-only subscriber.
            subscriber_id = uuid.uuid4().hex
            slot.subscribers.add(subscriber_id)
            return subscriber_id

    def release(self, key: OwnerKey, owner_id: str) -> bool:
        """Drop one owner. Returns True when the slot is now empty."""
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return True
            slot.subscribers.discard(owner_id)
            if slot.owner_id == owner_id:
                slot.mutating = False
                slot.owner_id = ""
                slot.session_id = ""
            if not slot.subscribers and not slot.mutating:
                del self._slots[key]
                return True
            return False

    def holder(self, key: OwnerKey) -> tuple[str, str] | None:
        """Return ``(owner_id, session_id)`` of the current mutating owner."""
        with self._lock:
            slot = self._slots.get(key)
            if slot is None or not slot.mutating:
                return None
            return slot.owner_id, slot.session_id

    def is_held(self, key: OwnerKey) -> bool:
        return self.holder(key) is not None

    def keys(self) -> Iterator[OwnerKey]:
        with self._lock:
            return iter(list(self._slots.keys()))

    def clear(self) -> None:
        with self._lock:
            self._slots.clear()


DEFAULT_OWNERSHIP = ExecutorRegistry()


class ProcessLease:
    """OS-held exclusion shared by legacy clients and native helpers.

    The lock is released by the OS after a crashed holder exits. A PID's
    lock is PID-scoped so standalone legacy clients use the same key.
    Process birth checks remain mandatory on every adapter operation.
    """
    def __init__(self, pid: int, birth: int):
        import os
        import tempfile
        from pathlib import Path
        root = Path(tempfile.gettempdir()) / "bughunter-memory-owners"
        root.mkdir(mode=0o700, exist_ok=True)
        self._file = (root / f"{pid}.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.write(b"\0"); self._file.flush(); self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            raise RuntimeError(f"target process {pid} is busy in another memory owner") from exc

    def close(self):
        if self._file.closed:
            return
        import os
        if os.name == "nt":
            import msvcrt
            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        self._file.close()


__all__ = ["AttachBusy", "ExecutorRegistry", "OwnerKey"]
