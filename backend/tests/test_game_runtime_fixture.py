"""P00 fixture + oracle + restart-harness tests.

The fixture, oracle and restart harness must behave identically across
Linux, macOS and Windows. The oracle's OS read path is best-effort:
where the host denies the capability (yama ptrace_scope on Linux, missing
debugger entitlement on macOS, unprivileged user on Windows) the oracle
raises :class:`OracleUnavailable` and the dependent tests skip with a
clear reason instead of silently passing.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import time

import pytest

from bughunter.game_runtime.testing import (
    DEFAULT_LAYOUT,
    Fixture,
    FixtureError,
    Oracle,
    OracleUnavailable,
    RestartHarness,
)


POINTER_FIELD = "pointer"
ENEMY_COUNT_FIELD = "enemy_count"


def _oracle_or_skip(identity) -> Oracle:
    try:
        return Oracle.from_identity(identity, layout=DEFAULT_LAYOUT)
    except OracleUnavailable as exc:
        pytest.skip(f"oracle unavailable on {sys.platform!r}: {exc}")


# ---------------------------------------------------------------------------
# Boot / identity
# ---------------------------------------------------------------------------


def test_fixture_boot_identity() -> None:
    with Fixture() as fx:
        identity = fx.identity()
        assert identity.pid != os.getpid()
        assert identity.pid > 0
        assert identity.start_time_ns > 0
        assert len(identity.module_fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in identity.module_fingerprint)
        names = [r.name for r in identity.region_map]
        assert names == [f"region_{i}" for i in range(DEFAULT_LAYOUT.region_count)]
        # bases must look like real addresses
        for region in identity.region_map:
            assert region.size == DEFAULT_LAYOUT.region_size
            assert region.base > 0

        identity2 = fx.identity()
        assert identity2.module_fingerprint == identity.module_fingerprint
        assert identity2.pid == identity.pid


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name, new_value",
    [
        ("alive_flag", 0),
        ("alive_flag", 1),
        ("countdown", 12345),
        ("player_x", 3.5),
        ("player_y", -2.25),
        ("enemy_count", 7),
    ],
)
def test_fixture_get_set(field_name: str, new_value) -> None:
    with Fixture() as fx:
        before = fx.get(field_name)
        fx.set(field_name, new_value)
        after = fx.get(field_name)
        if isinstance(new_value, float):
            assert pytest.approx(after, rel=1e-6, abs=1e-6) == new_value
        else:
            assert after == new_value
        assert after != before or before == new_value


def test_fixture_pointer_following() -> None:
    with Fixture() as fx:
        identity = fx.identity()
        region_1 = identity.region("region_1")
        pointer_value = fx.get(POINTER_FIELD)
        assert pointer_value == region_1.base + 0x200


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_fixture_shutdown_exits_cleanly() -> None:
    fx = Fixture()
    fx.start()
    try:
        identity = fx.identity()
        assert identity.pid > 0
    finally:
        start = time.monotonic()
        fx.shutdown(timeout=2.0)
        elapsed = time.monotonic() - start
    assert elapsed < 2.5, f"shutdown took {elapsed:.2f}s (expected < 2s)"
    # No zombie: the Popen object has observed the child exit.
    assert fx._proc is not None and fx._proc.poll() is not None


def _stalled_fixture(monkeypatch):
    from bughunter.game_runtime.testing import fixture_target
    popen = subprocess.Popen
    def stalled_child(_argv, **kwargs):
        # Read a command but never reply during the allowed response deadline.
        return popen([sys.executable, "-u", "-c",
                      "import sys,time; sys.stdin.readline(); time.sleep(2)"], **kwargs)
    monkeypatch.setattr(fixture_target.subprocess, "Popen", stalled_child)
    fx = Fixture()
    fx.start()
    return fx


def test_shutdown_deadline_includes_waiting_for_reply(monkeypatch):
    fx = _stalled_fixture(monkeypatch)
    started = time.monotonic()
    try:
        fx.shutdown(timeout=0.1)
        assert time.monotonic() - started < 1.5
        assert fx._proc.poll() is not None
        assert not fx._stdout_thread.is_alive()
    finally:
        if fx._proc.poll() is None:
            fx._proc.kill()
            fx._proc.wait(timeout=2)


def test_timed_out_rpc_cannot_consume_a_late_reply(monkeypatch):
    fx = _stalled_fixture(monkeypatch)
    try:
        with pytest.raises(FixtureError, match="response timed out"):
            fx._rpc({"method": "identity"}, timeout=0.1)
        with pytest.raises(FixtureError, match="unusable"):
            fx.identity()
    finally:
        fx.shutdown(timeout=0.1)


# ---------------------------------------------------------------------------
# Oracle independence
# ---------------------------------------------------------------------------


def test_oracle_reads_through_os_path() -> None:
    with Fixture() as fx:
        identity = fx.identity()
        oracle = _oracle_or_skip(identity)
        try:
            fx.set("countdown", 42)
            assert oracle.read_field("countdown") == 42
            fx.set("countdown", 99)
            assert oracle.read_field("countdown") == 99
            # enemy_count sits inside region_1, reachable via pointer field.
            assert oracle.read_field("enemy_count") == DEFAULT_LAYOUT.field("enemy_count").initial
        finally:
            oracle.close()


def test_oracle_independence_with_control_lie() -> None:
    """Fixture lies through get(); oracle reads memory and disagrees."""
    with Fixture(control_lie={"countdown": 777}) as fx:
        identity = fx.identity()
        try:
            oracle = Oracle.from_identity(identity, layout=DEFAULT_LAYOUT)
        except OracleUnavailable as exc:
            pytest.skip(
                "oracle has no OS read path on "
                f"{sys.platform!r} and cannot adjudicate the control-lie: {exc}"
            )
        try:
            fx.set("countdown", 42)
            fixture_view = fx.get("countdown")
            memory_view = oracle.read_field("countdown")
            assert fixture_view == 777, "control-lie did not take effect"
            assert memory_view == 42, (
                "oracle agreed with the fixture's lie; it is not reading memory "
                "independently"
            )
            assert fixture_view != memory_view
        finally:
            oracle.close()


# ---------------------------------------------------------------------------
# Restart harness
# ---------------------------------------------------------------------------


def test_restart_three_times_recipe_resolves() -> None:
    harness = RestartHarness()
    try:
        history = harness.restart(n=3, probe_fields=("alive_flag",))
    except OracleUnavailable as exc:
        pytest.skip(f"oracle unavailable: {exc}")

    if any(not obs.oracle_available for obs in history):
        reasons = [obs.oracle_reason for obs in history if not obs.oracle_available]
        pytest.skip(f"oracle unavailable during restart cycles: {reasons[0]}")

    pids = [obs.pid for obs in history]
    assert len(set(pids)) == len(pids), f"pids collided: {pids}"
    fingerprints = {obs.module_fingerprint for obs in history}
    assert len(fingerprints) == 1, f"module_fingerprint drifted across restarts: {fingerprints}"
    times = [obs.start_time_ns for obs in history]
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1)), (
        f"start_time_ns not strictly increasing across restarts: {times}"
    )
    for obs in history:
        assert obs.probes, "harness did not record any probes"
        probe = obs.probes[0]
        assert probe.module_rva_matches, (
            f"module+rva recipe failed to resolve on pid={obs.pid}: "
            f"value={probe.module_rva_value} expected={probe.expected_value}"
        )
        assert probe.module_rva_value == 1


def test_restart_three_times_recipe_breaks_after_move() -> None:
    """After moving region_0, the previous absolute address is stale.

    The module+rva recipe (resolved from a fresh identity) still works
    because it tracks the new base.
    """
    first_absolute_addr: int | None = None
    with Fixture() as fx:
        identity = fx.identity()
        try:
            oracle = Oracle.from_identity(identity)
        except OracleUnavailable as exc:
            pytest.skip(f"oracle unavailable: {exc}")
        try:
            alive_spec = DEFAULT_LAYOUT.field("alive_flag")
            region_0 = identity.region("region_0")
            first_absolute_addr = region_0.base + alive_spec.offset
            assert oracle.read_absolute(first_absolute_addr, "alive_flag") == 1

            # Re-randomise region_0.
            new_region_map = fx.move(["region_0"])
            new_base = {r.name: r.base for r in new_region_map}["region_0"]
            if new_base == region_0.base:
                pytest.skip(
                    "allocator returned the same base after move; cannot "
                    "exercise the absolute-recipe breakage on this host"
                )
            # Rebuild a fresh oracle so its cached region map matches the new layout.
            oracle.close()
            fresh_identity = fx.identity()
            oracle = Oracle.from_identity(fresh_identity)

            # module+rva recipe still resolves cleanly.
            assert oracle.read_field("alive_flag") == 1

            # The OLD absolute address either (a) now holds different bytes,
            # or (b) is unmapped and raises OracleUnavailable. Both are
            # "recipe no longer resolves".
            broken = False
            try:
                stale_value = oracle.read_absolute(first_absolute_addr, "alive_flag")
                if stale_value != 1:
                    broken = True
            except OracleUnavailable:
                broken = True
            assert broken, (
                f"absolute recipe at {first_absolute_addr:#x} still reads alive_flag=1 "
                "after move; move did not re-randomise the base"
            )
        finally:
            oracle.close()


# ---------------------------------------------------------------------------
# Negative: fixture RPC refuses malformed requests
# ---------------------------------------------------------------------------


def test_fixture_rejects_unknown_field() -> None:
    with Fixture() as fx:
        with pytest.raises(FixtureError):
            fx.get("does_not_exist")


def test_fixture_rejects_unknown_method() -> None:
    with Fixture() as fx:
        with pytest.raises(FixtureError):
            fx._rpc({"method": "nope"})
