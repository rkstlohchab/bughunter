"""Autonomous-mode flag.

When ``autonomy_mode`` is ``True`` (default), every approval / policy /
classification check in the codebase short-circuits:

- ``policy.classify_effect`` returns ``read_only`` for everything, so the
  investigation agent's ``_autodispatch`` auto-sends.
- Operator mode is enabled by default; every tool call executes without
  parking for approval.
- ``approved=True`` guards on write / configure / launch actions are
  skipped.

Toggle off by setting ``autonomy_mode=False`` in the ``settings`` table
if you ever want the full approval machinery back.
"""
from __future__ import annotations


def enabled(store) -> bool:
    """Return True if full autonomy is active. Default: True."""
    return bool(store.setting("autonomy_mode", True))


def require(approved: bool, store, message: str = "") -> None:
    """Replacement for ``if not approved: raise``. In autonomy mode this
    always passes; otherwise it enforces the approval."""
    if approved or enabled(store):
        return
    raise ValueError(message or "This action requires explicit approval.")
