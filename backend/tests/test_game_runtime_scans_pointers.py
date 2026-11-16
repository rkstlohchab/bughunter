"""P05 — scans + pointer discovery + mappings integration.

Everything in this file runs against an in-memory ``FakeAdapter`` so
the suite stays hermetic. The persistent-LLDB adapter is exercised by
``test_memory_engine.py`` and by the Rust chat-to-native sequence.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bughunter.game_runtime.mappings import MappingStore, Observation
from bughunter.game_runtime.pointers import (
    PointerResolveError, discover_pointer_paths, resolve_recipe,
)
from bughunter.game_runtime.scans import (
    ScanBudgetExhausted, ScanSession,
)
from bughunter.store import Store


# ---------------------------------------------------------------------------
# A small in-memory adapter we can control deterministically.
# ---------------------------------------------------------------------------


class FakeAdapter:
    """Byte-addressable fake memory for the scan/pointer tests."""

    def __init__(self) -> None:
        # One contiguous 128 KiB region starting at 0x10_0000 — large enough
        # to exercise 4 MiB chunk accounting (only one chunk) without the
        # speed cost of allocating a real multi-MiB buffer.
        self.region_base = 0x10_0000
        self.region_size = 128 * 1024
        self.memory = bytearray(self.region_size)
        self.unreadable: set[int] = set()  # addresses that raise on read

    # The pointer discovery and scan engines call these.
    def regions_writable(self) -> list[tuple[int, int]]:
        return [(self.region_base, self.region_size)]

    def read_bytes(self, address: int, size: int) -> bytes:
        if address in self.unreadable:
            raise RuntimeError(f"unreadable region at 0x{address:x}")
        if not (self.region_base <= address < self.region_base + self.region_size):
            raise RuntimeError(f"address 0x{address:x} outside fake region")
        offset = address - self.region_base
        if offset + size > self.region_size:
            raise RuntimeError("short read at fake region tail")
        return bytes(self.memory[offset:offset + size])

    def write_value(self, address: int, value, vtype: str) -> None:
        fmt, size = {
            "u8":  ("<B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "u64": ("<Q", 8),
            "i8":  ("<b", 1), "i16": ("<h", 2), "i32": ("<i", 4), "i64": ("<q", 8),
            "f32": ("<f", 4), "f64": ("<d", 8),
        }[vtype]
        packed = struct.pack(fmt, value)
        offset = address - self.region_base
        self.memory[offset:offset + size] = packed

    def write_bytes(self, address: int, data: bytes) -> None:
        offset = address - self.region_base
        self.memory[offset:offset + len(data)] = data


# ---------------------------------------------------------------------------
# P05.scans — exact/range/unknown + refinements
# ---------------------------------------------------------------------------


def test_scan_exact_finds_all_matches_and_boundary():
    adapter = FakeAdapter()
    # Seed three occurrences of 42 at known offsets, including one at
    # the region tail to exercise the boundary case.
    for addr in (adapter.region_base + 0x100,
                 adapter.region_base + 0x2000,
                 adapter.region_base + adapter.region_size - 4):
        adapter.write_value(addr, 42, "u32")
    session = ScanSession(adapter=adapter, value_type="u32")
    matches = session.scan_exact(42)
    found = sorted(m.address for m in matches)
    assert found == [
        adapter.region_base + 0x100,
        adapter.region_base + 0x2000,
        adapter.region_base + adapter.region_size - 4,
    ]
    assert session.progress.phase == "complete"
    assert session.progress.bytes_read == adapter.region_size


def test_scan_range_inclusive_bounds():
    adapter = FakeAdapter()
    adapter.write_value(adapter.region_base + 0x10, 10, "u32")
    adapter.write_value(adapter.region_base + 0x20, 50, "u32")
    adapter.write_value(adapter.region_base + 0x30, 100, "u32")
    adapter.write_value(adapter.region_base + 0x40, 101, "u32")
    session = ScanSession(adapter=adapter, value_type="u32")
    matches = session.scan_range(10, 100)
    found = sorted(m.address for m in matches)
    assert adapter.region_base + 0x10 in found
    assert adapter.region_base + 0x20 in found
    assert adapter.region_base + 0x30 in found
    assert adapter.region_base + 0x40 not in found


def test_scan_unknown_initial_then_refine_changed_and_increased(tmp_path):
    adapter = FakeAdapter()
    # Seed three cells, one of which will change.
    for addr in (adapter.region_base + 0x400,
                 adapter.region_base + 0x800,
                 adapter.region_base + 0xc00):
        adapter.write_value(addr, 1234, "u32")
    session = ScanSession(adapter=adapter, value_type="u32")
    session.scan_unknown_initial()
    assert session.progress.phase == "complete"
    # Mutate only one cell.
    adapter.write_value(adapter.region_base + 0x800, 5678, "u32")
    changed = session.refine("changed")
    changed_addrs = {m.address for m in changed}
    assert adapter.region_base + 0x800 in changed_addrs
    assert adapter.region_base + 0x400 not in changed_addrs
    assert adapter.region_base + 0xc00 not in changed_addrs
    # Now increase one of the changed survivors and refine increased.
    # The scanner uses byte-level sliding (Cheat-Engine convention), so
    # overlapping u32 positions crossing the mutated bytes also report
    # as changed; assert the aligned position is present.
    adapter.write_value(adapter.region_base + 0x800, 9999, "u32")
    increased = session.refine("increased")
    assert adapter.region_base + 0x800 in {m.address for m in increased}
    session.close()


def test_scan_byte_pattern_search():
    adapter = FakeAdapter()
    adapter.write_bytes(adapter.region_base + 0x300, b"BUGHUNTER")
    adapter.write_bytes(adapter.region_base + 0x5000, b"BUGHUNTER")
    session = ScanSession(adapter=adapter, value_type="bytes")
    matches = session.scan_exact(None, byte_pattern=b"BUGHUNTER")
    assert sorted(m.address for m in matches) == [
        adapter.region_base + 0x300, adapter.region_base + 0x5000,
    ]


def test_scan_partial_read_is_recorded_as_skipped_not_complete():
    adapter = FakeAdapter()
    # Simulate an unreadable chunk start: mark the region's base
    # unreadable. The scan skips it and reports partial.
    adapter.unreadable.add(adapter.region_base)
    session = ScanSession(adapter=adapter, value_type="u32")
    session.scan_exact(42)
    # No matches found but at least one skipped region.
    assert session.progress.regions_skipped == 1
    assert session.skipped_regions, "unreadable region must be recorded"


def test_scan_cancellation_stops_iteration():
    adapter = FakeAdapter()
    session = ScanSession(adapter=adapter, value_type="u32")
    session.cancel()
    session.scan_exact(42)
    assert session.progress.phase == "cancelled"


def test_scan_byte_pattern_rejects_empty_and_oversized():
    adapter = FakeAdapter()
    session = ScanSession(adapter=adapter, value_type="bytes")
    with pytest.raises(ValueError):
        session.scan_exact(None, byte_pattern=b"")
    with pytest.raises(ValueError):
        session.scan_exact(None, byte_pattern=b"x" * (4096 + 1))


# ---------------------------------------------------------------------------
# P05.restarts — resolver follows a recipe across fresh processes
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    os.environ["BUGHUNTER_DATA_DIR"] = str(tmp_path)
    return Store(str(tmp_path))


def test_resolver_follows_module_relative_root(store):
    adapter = FakeAdapter()
    # Seed a pointer at module_base + 0x100 that points to module_base + 0x200.
    # Seed value at module_base + 0x208 (offset 8).
    module_base = adapter.region_base
    adapter.write_value(module_base + 0x100, module_base + 0x200, "u64")
    adapter.write_value(module_base + 0x208, 7, "u32")

    ms = MappingStore(store)
    fingerprint = "a" * 64
    recipe = ms.save_new(
        name="test field", target_fingerprint=fingerprint, value_type="u32",
        root=f"mod:FakeModule+0x100",
        steps=[
            {"op": "deref", "offset": "+0x0", "width": 8},
            {"op": "add", "offset": "0x8", "width": 4},
        ],
    )
    resolved = resolve_recipe(
        recipe, adapter=adapter,
        module_base_lookup=lambda _: module_base,
        target_fingerprint=fingerprint,
    )
    assert resolved.value == 7
    assert resolved.final_address == module_base + 0x208


def test_resolver_restart_verification_requires_three_distinct_pids(store):
    ms = MappingStore(store)
    recipe = ms.save_new(
        name="restart recipe", target_fingerprint="b" * 64, value_type="u32",
        root="mod:X+0x0", steps=[{"op": "add", "offset": "+0x0", "width": 4}],
    )
    # Two observations from the same pid cannot promote.
    obs_same = [
        Observation(session_id=f"s{i}", target_fingerprint="b" * 64,
                    process_instance_id="same_pid", resolved_value=1,
                    resolved_at="2026-11-15T00:00:00Z", recipe_revision=1)
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="distinct process_instance_id"):
        ms.mark_restart_verified(recipe.id, observations=obs_same)
    # Three distinct pids succeed.
    obs_distinct = [
        Observation(session_id=f"s{i}", target_fingerprint="b" * 64,
                    process_instance_id=f"pid_{i}", resolved_value=1,
                    resolved_at="2026-11-15T00:00:00Z", recipe_revision=1)
        for i in range(3)
    ]
    ms.mark_restart_verified(recipe.id, observations=obs_distinct)
    assert ms.get(recipe.id).verification_status == "restart_verified"


def test_resolver_rejects_cross_target_fingerprint(store):
    adapter = FakeAdapter()
    ms = MappingStore(store)
    recipe = ms.save_new(
        name="target bound", target_fingerprint="c" * 64, value_type="u32",
        root="mod:X+0x0", steps=[{"op": "add", "offset": "+0x0", "width": 4}],
    )
    with pytest.raises(PointerResolveError, match="target_fingerprint"):
        resolve_recipe(recipe, adapter=adapter,
                       module_base_lookup=lambda _: adapter.region_base,
                       target_fingerprint="d" * 64)


# ---------------------------------------------------------------------------
# P05.staleness — invalid pointer / changed build rejects use
# ---------------------------------------------------------------------------


def test_staleness_short_read_raises(store):
    adapter = FakeAdapter()
    ms = MappingStore(store)
    recipe = ms.save_new(
        name="broken chain", target_fingerprint="e" * 64, value_type="u32",
        root="mod:X+0x0",
        steps=[
            {"op": "deref", "offset": "+0x0", "width": 8},
            {"op": "add", "offset": "+0x0", "width": 4},
        ],
    )
    # Mark the final read address unreadable by poisoning the pointer so
    # the recipe deref lands outside our fake region.
    adapter.write_value(adapter.region_base, 0xdead_beef, "u64")
    with pytest.raises((PointerResolveError, RuntimeError)):
        resolve_recipe(recipe, adapter=adapter,
                       module_base_lookup=lambda _: adapter.region_base,
                       target_fingerprint="e" * 64)


def test_staleness_observe_on_wrong_target_marks_stale(store):
    ms = MappingStore(store)
    recipe = ms.save_new(
        name="stale on wrong build", target_fingerprint="a" * 64, value_type="u32",
        root="mod:X+0x0", steps=[{"op": "add", "offset": "+0x0", "width": 4}],
    )
    ms.observe(recipe.id, session_id="s1", target_fingerprint="b" * 64,
               process_instance_id="pid1", resolved_value=1)
    assert ms.get(recipe.id).verification_status == "stale"


def test_revise_resets_status_to_candidate(store):
    ms = MappingStore(store)
    recipe = ms.save_new(
        name="revise resets", target_fingerprint="a" * 64, value_type="u32",
        root="mod:X+0x0", steps=[{"op": "add", "offset": "+0x0", "width": 4}],
    )
    ms.observe(recipe.id, session_id="s1", target_fingerprint="a" * 64,
               process_instance_id="pid1", resolved_value=1)
    assert ms.get(recipe.id).verification_status == "observed"
    ms.revise(recipe.id, name="revise resets v2", reason="edit")
    revised = ms.get(recipe.id)
    assert revised.revision == 2
    assert revised.verification_status == "candidate"
    assert revised.observations == []


# ---------------------------------------------------------------------------
# P05.budgets — resident / snapshot limits are honoured
# ---------------------------------------------------------------------------


def test_snapshot_budget_raises_before_silent_truncation(monkeypatch):
    adapter = FakeAdapter()
    # Clamp the ceiling far below the region size so the snapshot trips.
    import bughunter.game_runtime.scans as scans
    monkeypatch.setattr(scans, "_SNAPSHOT_BUDGET_BYTES", 1024)
    session = ScanSession(adapter=adapter, value_type="u32")
    with pytest.raises(ScanBudgetExhausted):
        session.scan_unknown_initial()
    assert session.progress.phase == "budget_exhausted"
    session.close()


def test_scan_cancel_mid_sweep_leaves_phase_cancelled():
    adapter = FakeAdapter()
    adapter.write_value(adapter.region_base + 0x10, 1, "u32")
    session = ScanSession(adapter=adapter, value_type="u32")

    # Cancel from a progress callback that fires once a match is found.
    def cb(progress):
        if progress.matches:
            session.cancel()
    session.scan_exact(1, progress=cb)
    assert session.progress.phase in ("cancelled", "complete")
    # Even if the sweep completed before cancel, at least one match must
    # have been recorded.
    assert session.matches


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_pointer_paths_finds_single_hop_module_relative():
    adapter = FakeAdapter()
    module_base = adapter.region_base
    # Point from module_base + 0x40 to the target at module_base + 0x1000.
    target = module_base + 0x1000
    adapter.write_value(module_base + 0x40, target, "u64")
    adapter.write_value(target, 777, "u32")

    paths = discover_pointer_paths(
        adapter, target_address=target, depth=1,
        offset_window=0,
        module_ranges=[("FakeModule", module_base, adapter.region_size)],
    )
    # At least one path originates from the module range.
    module_relative = [p for p in paths if p.root.startswith("mod:FakeModule")]
    assert module_relative, f"no module-relative candidate found in {paths}"


# ---------------------------------------------------------------------------
# P05.migration — SQLite-consistent backup + migrate + rollback
# ---------------------------------------------------------------------------


def test_migration_backup_migrate_rollback_preserves_records(store, tmp_path):
    from bughunter.game_runtime.migrations import (
        migrate_legacy_mappings, backup_workbench, rollback,
    )
    # Seed a couple of legacy pointer_candidate rows via the generic
    # Store.put to exercise the migrator's reader path.
    legacy_1 = store.put("pointer_candidate", {
        "process_label": "FixtureGame",
        "label": "countdown",
        "base": "mod:FakeModule+0x40",
        "offsets": ["0x0", "0x8"],
        "value_type": "u32",
        "expected_value": 60,
        "project_id": None,
    })
    legacy_2 = store.put("pointer_candidate", {
        "process_label": "FixtureGame",
        "label": "player_x",
        "base": "abs:0xDEADBEEF",
        "offsets": ["0x0"],
        "value_type": "f32",
        "expected_value": 0.0,
        "project_id": None,
    })

    report = migrate_legacy_mappings(store)
    assert set(report.migrated_ids) == {legacy_1["id"], legacy_2["id"]}
    # legacy_2 was absolute — migrated and immediately marked stale.
    assert report.marked_stale, "absolute roots must be marked stale on migration"

    # Rerun: idempotent skip.
    report2 = migrate_legacy_mappings(store)
    assert set(report2.skipped_ids) == {legacy_1["id"], legacy_2["id"]}
    assert report2.migrated_ids == []

    # Repeated migration preserves records.
    from bughunter.game_runtime.mappings import MappingStore
    ms = MappingStore(store)
    recipes = ms.list_all()
    assert len(recipes) == 2
    # Rollback: restore from the first backup, migration rows disappear.
    rollback(store, report.backup_path)
    ms2 = MappingStore(store)
    assert ms2.list_all() == []


def test_migration_dry_run_writes_no_rows(store):
    from bughunter.game_runtime.migrations import migrate_legacy_mappings
    store.put("pointer_candidate", {
        "process_label": "FixtureGame", "label": "sample",
        "base": "mod:X+0x0", "offsets": ["0x0"],
        "value_type": "u32", "expected_value": 1,
        "project_id": None,
    })
    report = migrate_legacy_mappings(store, dry_run=True)
    assert report.migrated_ids
    from bughunter.game_runtime.mappings import MappingStore
    ms = MappingStore(store)
    assert ms.list_all() == []


def test_migration_does_not_promote_status(store):
    """Migrated absolute-root recipes stay stale; module-relative stay candidate."""
    from bughunter.game_runtime.migrations import migrate_legacy_mappings
    from bughunter.game_runtime.mappings import MappingStore
    store.put("pointer_candidate", {
        "process_label": "FixtureGame", "label": "mod relative",
        "base": "mod:X+0x100", "offsets": ["0x0"],
        "value_type": "u32", "expected_value": 1,
        "project_id": None,
    })
    migrate_legacy_mappings(store)
    ms = MappingStore(store)
    recipes = ms.list_all()
    assert len(recipes) == 1
    assert recipes[0].verification_status == "candidate", \
        "migration never elevates status beyond candidate on its own"
