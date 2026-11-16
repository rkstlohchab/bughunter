"""P06 — feature lifecycle tests against the P00 fixture.

The fixture carries a ``countdown`` field at a known offset that we
treat as a stand-in for Among Us's ``PlayerControl.killTimer``. These
tests cover:

  * baseline → enabled → disabled cycle;
  * readback verification refuses silent short writes;
  * identity change (process_instance_id drift) stops the feature;
  * stop() does NOT restore a stale captured countdown (plan rule).

The actual live Among Us acceptance runs ``scripts/verify_game_feature.py``
against the installed build; see
``docs/game-lab/P06-handoff.md`` for the human-observed gameplay gate.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bughunter.game_runtime.among_us import (
    KILL_TIMER_INSTANCE_OFFSET, build_kill_cooldown_recipe,
    plausible_kill_timer, scan_for_local_player_candidates,
)
from bughunter.game_runtime.features import FeatureContext, FeatureRun
from bughunter.game_runtime.mappings import MappingStore
from bughunter.game_runtime.pointers import resolve_recipe
from bughunter.store import Store


class FakeAdapter:
    """Byte-addressable fake for the lifecycle tests."""
    def __init__(self):
        self.region_base = 0x10_0000
        self.region_size = 64 * 1024
        self.memory = bytearray(self.region_size)

    def regions_writable(self):
        return [(self.region_base, self.region_size)]

    def read_bytes(self, address, size):
        offset = address - self.region_base
        if offset < 0 or offset + size > self.region_size:
            raise RuntimeError(f"short read at 0x{address:x}")
        return bytes(self.memory[offset:offset + size])

    def write_value(self, address, value, vtype, verify=True):
        fmt = {"f32": "<f", "u32": "<I"}[vtype]
        offset = address - self.region_base
        self.memory[offset:offset + 4] = struct.pack(fmt, value)
        if verify:
            read = struct.unpack(fmt, bytes(self.memory[offset:offset + 4]))[0]
            if read != value:
                raise RuntimeError("readback mismatch")

    def compare_write_value(self, address, expected, new, vtype):
        current = struct.unpack("<f" if vtype == "f32" else "<I", self.read_bytes(address, 4))[0]
        if current != expected:
            raise RuntimeError(f"precondition_failed: expected {expected}, got {current}")
        self.write_value(address, new, vtype, verify=True)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BUGHUNTER_DATA_DIR", str(tmp_path))
    return Store(str(tmp_path))


def _setup_recipe_and_adapter(store):
    adapter = FakeAdapter()
    module_base = adapter.region_base
    # Simulate the PlayerControl static-fields layout: at module_base + 0x100
    # there is a pointer to a PlayerControl instance at module_base + 0x400.
    # killTimer at instance + 0xB0 = module_base + 0x4B0, start at 20.0s.
    instance = module_base + 0x400
    # Pack LocalPlayer* pointer.
    adapter.memory[0x100:0x108] = struct.pack("<Q", instance)
    adapter.write_value(instance + KILL_TIMER_INSTANCE_OFFSET, 20.0, "f32")

    ms = MappingStore(store)
    fingerprint = "a" * 64
    recipe = ms.save_new(
        name="fixture cooldown", target_fingerprint=fingerprint, value_type="f32",
        root=f"mod:FakeModule+0x100",
        steps=[
            {"op": "deref", "offset": "+0x0", "width": 8},
            {"op": "add", "offset": f"0x{KILL_TIMER_INSTANCE_OFFSET:x}", "width": 4},
        ],
    )

    ctx = FeatureContext(
        adapter=adapter,
        module_base_lookup=lambda _: module_base,
        target_fingerprint=fingerprint,
        process_instance_id="pid-1:birth-1",
    )
    return adapter, ms, recipe, ctx, module_base, instance


def test_feature_start_captures_baseline(store):
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="no_cooldown", configuration={})
    run.start(ctx)
    assert run.state == "running"
    assert run.baseline_value == pytest.approx(20.0)


def test_feature_apply_writes_and_verifies(store):
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="no_cooldown", configuration={})
    run.start(ctx)
    confirmed = run.apply(ctx, 0.0, disable_value=None)
    assert confirmed == pytest.approx(0.0)
    assert run.state == "monitoring"
    # Verify the write landed via the resolver's address.
    resolved = resolve_recipe(recipe, adapter=adapter,
                              module_base_lookup=lambda _: module_base,
                              target_fingerprint="a" * 64)
    assert resolved.value == pytest.approx(0.0)


def test_feature_stop_does_not_restore_stale_countdown(store):
    """Plan rule: never silently write an old countdown back."""
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="no_cooldown", configuration={})
    run.start(ctx)
    run.apply(ctx, 0.0, disable_value=None)
    run.stop(ctx)
    # Field stayed at the applied value — stop did NOT overwrite with the
    # captured baseline (which is stale once time has elapsed).
    resolved = resolve_recipe(recipe, adapter=adapter,
                              module_base_lookup=lambda _: module_base,
                              target_fingerprint="a" * 64)
    assert resolved.value == pytest.approx(0.0)


def test_feature_stop_writes_declared_disable_value_when_safe(store):
    """If the operator declared a disable_value, stop() uses it when
    the applied value is still in place."""
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="manual", configuration={})
    run.start(ctx)
    run.apply(ctx, 7.0, disable_value=20.0)
    run.stop(ctx)
    resolved = resolve_recipe(recipe, adapter=adapter,
                              module_base_lookup=lambda _: module_base,
                              target_fingerprint="a" * 64)
    assert resolved.value == pytest.approx(20.0)


def test_feature_stop_skips_cleanup_if_object_changed(store):
    """If the live value changed out from under us (round transition,
    new player), stop() does NOT overwrite it."""
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="manual", configuration={})
    run.start(ctx)
    run.apply(ctx, 7.0, disable_value=20.0)
    # External mutation — simulate round transition resetting the field.
    adapter.write_value(instance + KILL_TIMER_INSTANCE_OFFSET, 9.5, "f32")
    run.stop(ctx)
    # Cleanup was skipped because current != applied_value.
    resolved = resolve_recipe(recipe, adapter=adapter,
                              module_base_lookup=lambda _: module_base,
                              target_fingerprint="a" * 64)
    assert resolved.value == pytest.approx(9.5)


def test_feature_identity_change_blocks_further_writes(store):
    adapter, ms, recipe, ctx, module_base, instance = _setup_recipe_and_adapter(store)
    run = FeatureRun(recipe=recipe, feature_id="no_cooldown", configuration={})
    run.start(ctx)
    # Identity drifted (new process spawned).
    ctx.process_instance_id = "pid-2:birth-2"
    with pytest.raises(RuntimeError, match="identity_changed"):
        run.apply(ctx, 0.0)
    assert run.state == "stopping"


def test_among_us_build_recipe_defaults_to_placeholder_root(store):
    recipe = build_kill_cooldown_recipe(
        store, target_fingerprint="3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992",
    )
    assert recipe.root == "placeholder"
    assert recipe.verification_status == "candidate"
    assert len(recipe.steps) == 2
    assert recipe.steps[-1]["offset"] == f"0x{KILL_TIMER_INSTANCE_OFFSET:x}"


def test_among_us_build_recipe_module_relative(store):
    recipe = build_kill_cooldown_recipe(
        store, target_fingerprint="3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992",
        player_control_class_static_fields_rva=0x12345,
    )
    assert recipe.root.startswith("mod:UnityFramework+0x")
    assert recipe.verification_status == "candidate"


def test_among_us_scan_finds_candidate_at_plausible_cooldown(store):
    adapter, _, _, _, module_base, instance = _setup_recipe_and_adapter(store)
    # Set killTimer to 20.0 (a default setting) and scan.
    adapter.write_value(instance + KILL_TIMER_INSTANCE_OFFSET, 20.0, "f32")
    candidates = scan_for_local_player_candidates(adapter)
    assert any(c.address == instance for c in candidates), \
        f"instance 0x{instance:x} should be a candidate"


def test_among_us_plausible_kill_timer_bounds():
    assert plausible_kill_timer(0.0)
    assert plausible_kill_timer(20.0)
    assert plausible_kill_timer(120.0)
    assert not plausible_kill_timer(-0.5)
    assert not plausible_kill_timer(999.0)
    assert not plausible_kill_timer("20.0")
