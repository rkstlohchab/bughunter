"""Restart harness for the disposable fixture target.

The :class:`RestartHarness` boots the fixture, lets the oracle observe
it, shuts it down, and does that ``n`` times. On each cycle it records:

* the new process ``pid``,
* the ``module_fingerprint`` (should be stable across restarts),
* ``start_time_ns`` (should be monotonically increasing on the same
  boot because every child reads the same ``CLOCK_MONOTONIC_RAW``),
* whether a saved recipe — expressed as a ``(region_name, offset)``
  pair resolved from the fresh identity — still finds the expected
  value in memory after the restart,
* whether the SAME absolute address that resolved the recipe on the
  previous boot still does.

The absolute-address probe is expected to fail after a region move
(because ASLR / allocator randomisation parks the new mapping at a
different base). That failure is how the harness proves the recipe
semantics P03/P05 rely on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .fixture_target import Fixture, IdentityResponse
from .layout import DEFAULT_LAYOUT, Layout
from .oracle import Oracle, OracleUnavailable


@dataclass
class RecipeProbe:
    """Snapshot of a saved recipe probed against one restart."""

    field_name: str
    expected_value: Any
    # Fresh (region_name, offset) still resolves via the new identity.
    module_rva_value: Any = None
    module_rva_matches: bool = False
    # Absolute address captured on the FIRST boot. After a restart it
    # usually no longer points at the same field — that is the point of
    # the probe.
    absolute_addr: int | None = None
    absolute_value: Any = None
    absolute_matches: bool = False
    note: str = ""


@dataclass
class RestartObservation:
    pid: int
    start_time_ns: int
    module_fingerprint: str
    region_bases: dict[str, int] = field(default_factory=dict)
    probes: list[RecipeProbe] = field(default_factory=list)
    oracle_available: bool = True
    oracle_reason: str = ""


class RestartHarness:
    """Drive ``n`` fixture restarts and record recipe-resolution behaviour."""

    def __init__(
        self,
        *,
        layout: Layout = DEFAULT_LAYOUT,
        fixture_factory: Callable[[], Fixture] | None = None,
    ) -> None:
        self.layout = layout
        self._fixture_factory = fixture_factory or (lambda: Fixture())

    def restart(self, n: int = 3, *, probe_fields: tuple[str, ...] = ("alive_flag",)) -> list[RestartObservation]:
        if n < 1:
            raise ValueError("n must be >= 1")
        history: list[RestartObservation] = []
        saved_absolute: dict[str, int] = {}
        saved_expected: dict[str, Any] = {}
        for cycle in range(n):
            observation = self._cycle(cycle, probe_fields, saved_absolute, saved_expected)
            history.append(observation)
        return history

    # ------------------------------------------------------------------

    def _cycle(
        self,
        cycle: int,
        probe_fields: tuple[str, ...],
        saved_absolute: dict[str, int],
        saved_expected: dict[str, Any],
    ) -> RestartObservation:
        with self._fixture_factory() as fixture:
            identity = fixture.identity()
            obs = RestartObservation(
                pid=identity.pid,
                start_time_ns=identity.start_time_ns,
                module_fingerprint=identity.module_fingerprint,
                region_bases={r.name: r.base for r in identity.region_map},
            )
            try:
                oracle = Oracle.from_identity(identity, layout=self.layout)
            except OracleUnavailable as exc:
                obs.oracle_available = False
                obs.oracle_reason = str(exc)
                return obs
            try:
                for field_name in probe_fields:
                    probe = self._probe(
                        oracle=oracle,
                        identity=identity,
                        field_name=field_name,
                        cycle=cycle,
                        saved_absolute=saved_absolute,
                        saved_expected=saved_expected,
                    )
                    obs.probes.append(probe)
            finally:
                oracle.close()
            return obs

    def _probe(
        self,
        *,
        oracle: Oracle,
        identity: IdentityResponse,
        field_name: str,
        cycle: int,
        saved_absolute: dict[str, int],
        saved_expected: dict[str, Any],
    ) -> RecipeProbe:
        spec = self.layout.field(field_name)
        region = identity.region(spec.region)
        fresh_addr = region.base + spec.offset

        try:
            module_rva_value = oracle.read_field(field_name)
            module_rva_err = ""
        except OracleUnavailable as exc:
            module_rva_value = None
            module_rva_err = str(exc)

        probe = RecipeProbe(
            field_name=field_name,
            expected_value=saved_expected.get(field_name, module_rva_value),
            module_rva_value=module_rva_value,
            absolute_addr=saved_absolute.get(field_name),
        )
        probe.module_rva_matches = (
            module_rva_value is not None and module_rva_value == probe.expected_value
        )
        if probe.absolute_addr is not None:
            try:
                probe.absolute_value = oracle.read_absolute(probe.absolute_addr, field_name)
                probe.absolute_matches = probe.absolute_value == probe.expected_value
            except OracleUnavailable as exc:
                probe.absolute_value = None
                probe.absolute_matches = False
                probe.note = f"absolute read failed: {exc}"
        else:
            probe.note = "absolute recipe not yet captured"
        # Record for next cycle.
        saved_absolute[field_name] = fresh_addr
        saved_expected.setdefault(field_name, module_rva_value)
        if module_rva_err:
            probe.note = probe.note or module_rva_err
        _ = cycle  # unused but kept for future-dated diagnostics
        return probe


__all__ = ["RestartHarness", "RestartObservation", "RecipeProbe"]
