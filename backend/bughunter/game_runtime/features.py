"""P06 — feature lifecycle machinery.

A :class:`FeatureRun` orchestrates the start/inspect/apply/verify/
monitor/stop sequence for a single gameplay feature. Features are
scoped to a :class:`~.mappings.Recipe` plus a specific target process;
they never write to a stale object identity.

Lifecycle states follow the plan:

    requested -> starting -> running -> verifying ->
                 monitoring -> stopping -> stopped

Side transitions:
  * running -> failed (precondition check, target dead, etc.)
  * monitoring -> stopping (round transition, death, disconnect)
  * any -> blocked (precondition unsatisfied)

A feature NEVER restores an old captured countdown value after time
has elapsed — the researched cleanup procedure is either a fresh
baseline write or a documented round-restart requirement.
"""
from __future__ import annotations

import time
import math
import struct
import threading
from functools import wraps
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .mappings import MappingStore, Recipe
from .pointers import PointerResolveError, resolve_recipe


_STATES = ("requested", "starting", "running", "verifying",
           "monitoring", "stopping", "stopped", "failed", "blocked")


@dataclass
class FeatureEvent:
    at: float
    kind: str  # 'read'|'write'|'verify'|'state'|'cleanup'|'error'
    detail: dict = field(default_factory=dict)


@dataclass
class FeatureContext:
    """Operator-visible context the feature needs each tick."""
    adapter: Any
    module_base_lookup: Callable[[str], int]
    target_fingerprint: str
    process_instance_id: str
    now: Callable[[], float] = time.monotonic
    validate_runtime: Optional[Callable[[], None]] = None


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


@dataclass
class FeatureRun:
    recipe: Recipe
    feature_id: str
    configuration: dict
    state: str = 'requested'
    last_error: str = ''
    baseline_value: Any = None
    applied_value: Any = None
    disable_value: Any = None
    events: list[FeatureEvent] = field(default_factory=list)
    cleanup_status: str = 'not_requested'
    _original_identity: str = ''
    _object_path: tuple = ()
    _stop_requested: bool = False
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    def _event(self, kind, **detail):
        self.events.append(FeatureEvent(time.monotonic(), kind, detail))
        if len(self.events) > 10000:
            del self.events[:1000]

    def _set(self, state):
        self.state = state
        self._event('state', state=state)

    def _check_identity(self, ctx):
        if self._original_identity and ctx.process_instance_id != self._original_identity:
            self.last_error = 'identity_changed: target process differs from feature owner'
            self._set('stopping')
            raise RuntimeError(self.last_error)
        if ctx.validate_runtime is not None:
            ctx.validate_runtime()

    def _resolve(self, ctx):
        self._check_identity(ctx)
        resolved = resolve_recipe(self.recipe, adapter=ctx.adapter,
            module_base_lookup=ctx.module_base_lookup, target_fingerprint=ctx.target_fingerprint,
            process_instance_id=ctx.process_instance_id)
        path = tuple(resolved.intermediate_addresses)
        if self._object_path and path != self._object_path:
            self.last_error = 'object_changed: resolver now points to a different object'
            self._set('stopping')
            raise RuntimeError(self.last_error)
        self._object_path = path
        return resolved

    @_serialized
    def start(self, ctx):
        if self.state != 'requested':
            raise RuntimeError('feature can only start once')
        self._set('starting')
        self._original_identity = ctx.process_instance_id
        try:
            self.baseline_value = self.inspect(ctx)
            self._set('running')
        except Exception as exc:
            self.last_error = str(exc)
            self._set('failed')
            raise

    @_serialized
    def inspect(self, ctx, *, store_baseline=False):
        resolved = self._resolve(ctx)
        self._event('read', value=resolved.value, address=hex(resolved.final_address))
        if store_baseline:
            self.baseline_value = resolved.value
        return resolved.value

    def _write(self, ctx, resolved, value):
        if not callable(getattr(ctx.adapter, 'compare_write_value', None)):
            raise RuntimeError('adapter has no conditional write operation')
        from .pointers import _SCALAR_FMT
        fmt, _ = _SCALAR_FMT[self.recipe.value_type]
        value = struct.unpack(fmt, struct.pack(fmt, value))[0]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('feature values must be finite')
        # Compare and readback must address the same pinned object.
        ctx.adapter.compare_write_value(resolved.final_address, resolved.value, value, self.recipe.value_type)
        confirmed = self._resolve(ctx)
        if confirmed.value != value:
            raise RuntimeError(f'readback_mismatch: wrote {value!r}, read {confirmed.value!r}')
        self._event('write', value=value, address=hex(resolved.final_address), confirmed=confirmed.value)
        return value

    @_serialized
    def apply(self, ctx, new_value, *, disable_value=None):
        if self._stop_requested or self.state not in ('running', 'monitoring'):
            raise RuntimeError(f'apply not valid in state {self.state}')
        self._check_identity(ctx)
        self._set('verifying')
        try:
            resolved = self._resolve(ctx)
            self.applied_value = self._write(ctx, resolved, new_value)
            self.disable_value = disable_value
            self._set('monitoring')
            return self.applied_value
        except Exception as exc:
            self.last_error = str(exc)
            self._event('error', stage='apply', error=self.last_error)
            if self.state != 'stopping':
                self._set('failed')
            raise

    @_serialized
    def monitor(self, ctx):
        if self.state not in ('running', 'monitoring') or self._stop_requested:
            raise RuntimeError(f'monitor not valid in state {self.state}')
        try:
            return self.inspect(ctx)
        except Exception as exc:
            self.last_error = str(exc)
            self._set('stopping')
            raise

    @_serialized
    def maintain(self, ctx):
        """One explicitly scheduled tick; never replays a failed/unknown write."""
        current = self.monitor(ctx)
        if self.applied_value is not None and current != self.applied_value:
            self.apply(ctx, self.applied_value, disable_value=self.disable_value)
        return self.inspect(ctx)

    @_serialized
    def stop(self, ctx, *, write_cleanup=True):
        if self._stop_requested:
            return None
        self._stop_requested = True
        self._set('stopping')
        restored = None
        try:
            if write_cleanup and self.disable_value is not None:
                resolved = self._resolve(ctx)
                if resolved.value != self.applied_value:
                    self.cleanup_status = 'skipped'
                    self._event('cleanup', status='skipped', reason='value_changed')
                else:
                    restored = self._write(ctx, resolved, self.disable_value)
                    self.cleanup_status = 'pass'
                    self._event('cleanup', status='pass', value=restored)
            else:
                self.cleanup_status = 'no_write'
                self._event('cleanup', status='no_write', reason='natural reset or round restart required')
        except Exception as exc:
            self.last_error = str(exc)
            self.cleanup_status = 'fail'
            self._event('error', stage='stop', error=self.last_error)
            self._set('failed')
            raise
        self._set('stopped')
        return restored


__all__ = ['FeatureRun', 'FeatureContext', 'FeatureEvent']
