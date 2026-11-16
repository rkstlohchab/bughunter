"""Cross-platform disposable target fixture + independent memory oracle.

This package is a P00 deliverable reused by P03/P05/P06. It provides:

- :mod:`fixture_process` — the child entrypoint, run as
  ``python -m bughunter.game_runtime.testing.fixture_process``. It allocates
  anonymous memory regions with a deterministic layout and answers a
  line-delimited JSON RPC on stdin/stdout.
- :mod:`fixture_target` — the parent-side helper: ``Fixture`` context
  manager that launches the subprocess and exposes identity/get/set/move/
  shutdown.
- :mod:`oracle` — an *independent* reader that does not go through
  ``Fixture.get``. It reads process memory via the OS (``/proc/<pid>/mem``
  on Linux, ``mach_vm_read_overwrite`` on macOS, ``ReadProcessMemory`` on
  Windows). If no path is available on the current platform/entitlement, it
  raises :class:`OracleUnavailable` so tests can skip with a clear reason.
- :mod:`restart_harness` — ``RestartHarness`` that boots → observes →
  shuts down the fixture a configurable number of times and records how
  recipes survive re-randomised bases.

All layout constants live in :data:`DEFAULT_LAYOUT` so P03/P05/P06 can
share the same field map and so recipes can be written against stable
offsets rather than per-platform addresses.
"""
from __future__ import annotations

from .fixture_target import Fixture, FixtureError, IdentityResponse, RegionInfo
from .oracle import Oracle, OracleUnavailable
from .restart_harness import RestartHarness, RestartObservation, RecipeProbe
from .layout import DEFAULT_LAYOUT, FieldSpec, Layout, REGION_SIZE

__all__ = [
    "Fixture",
    "FixtureError",
    "IdentityResponse",
    "RegionInfo",
    "Oracle",
    "OracleUnavailable",
    "RestartHarness",
    "RestartObservation",
    "RecipeProbe",
    "DEFAULT_LAYOUT",
    "FieldSpec",
    "Layout",
    "REGION_SIZE",
]
