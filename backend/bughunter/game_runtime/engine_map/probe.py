"""Common shape for multi-phase, resumable engine probes."""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable

from .memview import Sweep

_NAME = re.compile(r"[A-Za-z_<][A-Za-z0-9_<>`.$|=\-]{0,199}")


def check_name(value) -> bytes:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("name must be an identifier (letters, digits, _<>`.$|=-), 1..200 characters")
    return value.encode("ascii")


def round8(value: int) -> int:
    return (value + 7) // 8 * 8


class Probe:
    """Subclasses set ``phase`` and ``sweep`` and implement ``_step``.

    ``advance`` runs phases until done, ``stop()`` is true, or the reader
    raises a budget/cancel error (left to the caller, state preserved).
    """

    kind = "probe"

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.phase = "start"
        self.sweep: Sweep | None = None
        self.result: dict | None = None
        self.findings: list[dict] = []
        self.notes: list[str] = []

    @property
    def done(self) -> bool:
        return self.phase == "done"

    def advance(self, reader, stop: Callable[[], bool] | None = None) -> None:
        while not self.done:
            before = self.phase
            finished_phase = self._step(reader, stop)
            if not finished_phase or (self.phase == before and not self.done):
                return

    def _step(self, reader, stop) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def status(self) -> dict:
        return {"job_id": self.id, "probe": self.kind, "phase": self.phase, "done": self.done,
                "progress": self.sweep.progress() if self.sweep is not None else None,
                "skipped_regions": (self.sweep.skipped[:50] if self.sweep is not None else []),
                "notes": self.notes[-10:], "result": self.result}
