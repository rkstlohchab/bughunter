"""Unified timeline — every tool call, HTTP request, model turn, approval.

The existing ``events`` table is append-only; we layer a typed timeline
on top so UI can show one scrollable stream per run (or across all runs)
with filter / search / replay.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .store import Store, new_id, now


class Timeline:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "timeline.list":
            return self.query(**(params or {}))
        if method == "timeline.get":
            return self.get(params["id"])
        if method == "timeline.search":
            return self.search(**params)
        if method == "timeline.stream_since":
            return self.since(**params)
        raise ValueError(f"Unknown timeline method: {method}")

    # emit APIs (callers) -------------------------------------------------

    def emit(self, *, kind: str, phase: str, run_id: str | None = None,
             tool: str | None = None, payload: dict | None = None,
             outcome: str | None = None, duration_ms: int | None = None) -> dict:
        event_id = new_id("tl")
        record = {
            "id": event_id,
            "kind": kind,                 # tool_call | http | model_turn | approval | adapter | workflow
            "phase": phase,               # start | complete | error | denied | queued
            "run_id": run_id,
            "tool": tool,
            "payload": (payload or {}),
            "outcome": outcome,
            "duration_ms": duration_ms,
            "ts": now(),
            "ts_epoch": time.time(),
        }
        self.store.put("timeline_event", record, id=event_id)
        # mirror a thin version into the events ledger for existing tooling
        self.store.event(f"timeline.{kind}.{phase}",
                         {"id": event_id, "tool": tool, "outcome": outcome})
        return record

    def wrap(self, *, kind: str, tool: str,
             run_id: str | None = None,
             payload: dict | None = None):
        """Context manager: emits start + complete/error with duration."""
        start = time.time()
        start_rec = self.emit(kind=kind, phase="start", tool=tool,
                              run_id=run_id, payload=payload)

        class _Ctx:
            def __init__(self, tl: Timeline, parent: str):
                self.tl = tl
                self.parent = parent

            def __enter__(self):
                return self

            def __exit__(self_inner, exc_type, exc, tb):
                dur = int((time.time() - start) * 1000)
                if exc:
                    self.emit(kind=kind, phase="error", tool=tool,
                              run_id=run_id, payload={"error": repr(exc)},
                              outcome="error", duration_ms=dur)
                else:
                    self.emit(kind=kind, phase="complete", tool=tool,
                              run_id=run_id, outcome="ok", duration_ms=dur)
                return False
        return _Ctx(self, start_rec["id"])

    # queries -------------------------------------------------------------

    def query(self, *, run_id: str | None = None, kind: str | None = None,
              phase: str | None = None, limit: int = 500,
              **_) -> list[dict]:
        events = self.store.list("timeline_event")
        if run_id:
            events = [e for e in events if e.get("run_id") == run_id]
        if kind:
            events = [e for e in events if e.get("kind") == kind]
        if phase:
            events = [e for e in events if e.get("phase") == phase]
        events.sort(key=lambda e: e.get("ts_epoch", 0), reverse=True)
        return events[:limit]

    def get(self, event_id: str) -> dict | None:
        return self.store.get("timeline_event", event_id)

    def search(self, *, query: str, limit: int = 100, **_) -> list[dict]:
        needle = (query or "").lower()
        if not needle:
            return []
        hits = []
        for e in self.store.list("timeline_event"):
            blob = json.dumps(e, default=str).lower()
            if needle in blob:
                hits.append(e)
                if len(hits) >= limit:
                    break
        return hits

    def since(self, *, run_id: str | None = None,
              since_epoch: float = 0, limit: int = 200, **_) -> list[dict]:
        events = self.store.list("timeline_event")
        if run_id:
            events = [e for e in events if e.get("run_id") == run_id]
        events = [e for e in events if (e.get("ts_epoch") or 0) > since_epoch]
        events.sort(key=lambda e: e.get("ts_epoch", 0))
        return events[:limit]
