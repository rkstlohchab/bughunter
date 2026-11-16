"""Mapping transfer preserves provenance without transferring runtime trust."""
from __future__ import annotations

import copy
import json

import pytest

from bughunter.game_runtime import mappings
from bughunter.game_runtime.mappings import MappingStore, Observation
from bughunter.game_runtime.migrations import (
    backup_workbench, migrate_legacy_mappings, rollback,
)
from bughunter.store import Store


@pytest.fixture
def mapping_store(tmp_path):
    return MappingStore(Store(tmp_path / "data"))


def _recipe(ms, name="transfer field", **kwargs):
    return ms.save_new(
        name=name, target_fingerprint="a" * 64, value_type="u32",
        root=kwargs.pop("root", "mod:Fixture+0x100"),
        steps=[{"op": "add", "offset": "-0x8", "width": 8}],
        preconditions=["module_present:Fixture"],
        evidence_ids=kwargs.pop("evidence_ids", ["capture_1"]), **kwargs,
    )


def _event_count(ms):
    with ms.store.connection() as db:
        return db.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def test_round_trip_fresh_ids_revision_and_provenance_without_runtime_trust(mapping_store):
    ms = mapping_store
    original = _recipe(ms)
    original = ms.revise(original.id, name="revised field")
    observations = [
        Observation(f"session_{i}", "a" * 64, f"{i}:100", 42,
                    "2026-11-16T00:00:00+00:00", original.revision)
        for i in range(3)
    ]
    ms.mark_restart_verified(original.id, observations=observations)
    before = ms.get(original.id)
    exported = ms.export_recipes([original.id])
    assert json.loads(json.dumps(exported, allow_nan=False)) == exported
    assert exported["recipes"][0]["verification_status"] == "restart_verified"

    imported = ms.import_recipes(exported)[0]
    repeated = ms.import_recipes(exported)[0]
    assert len({original.id, imported.id, repeated.id}) == 3
    assert imported.revision == 1
    assert imported.verification_status == "candidate"
    assert imported.observations == []
    assert imported.evidence_ids == ["capture_1", original.id]
    assert imported.steps == before.steps
    assert imported.preconditions == before.preconditions
    provenance = imported.history[0]["provenance"]
    assert provenance["kind"] == "unverified_import"
    assert provenance["source_recipe"] == exported["recipes"][0]
    assert all(entry["status_at"] == "candidate" for entry in imported.history)
    with pytest.raises(ValueError, match="distinct process_instance_id"):
        ms.mark_restart_verified(imported.id, observations=imported.observations)
    assert ms.get(original.id) == before
    # Mutating the input/result never changes the stored provenance.
    exported["recipes"][0]["history"].clear()
    imported.history.clear()
    assert ms.get(imported.id).history[0]["provenance"] == provenance


def test_import_rebinds_fingerprint_but_absolute_roots_remain_stale(mapping_store):
    ms = mapping_store
    absolute = _recipe(ms, root="abs:0x12345678")
    payload = ms.export_recipes([absolute.id])
    imported = ms.import_recipes(payload, target_fingerprint="B" * 64)[0]
    assert imported.target_fingerprint == "b" * 64
    assert imported.root == absolute.root
    assert imported.verification_status == "stale"
    assert imported.observations == []
    assert imported.history[0]["provenance"]["source_recipe"]["target_fingerprint"] == "a" * 64
    with pytest.raises(ValueError, match="stale"):
        ms.mark_restart_verified(imported.id, observations=[])


def test_export_selection_empty_and_missing_ids(mapping_store):
    ms = mapping_store
    one = _recipe(ms, "one field")
    two = _recipe(ms, "two field")
    assert ms.export_recipes([]) == {"schema_version": 1, "recipes": []}
    events = _event_count(ms)
    assert ms.import_recipes({"schema_version": 1, "recipes": []}) == []
    assert _event_count(ms) == events
    selected = ms.export_recipes([two.id, one.id, two.id])
    assert [row["id"] for row in selected["recipes"]] == [two.id, one.id]
    with pytest.raises(KeyError, match="missing"):
        ms.export_recipes([one.id, "missing"])
    with pytest.raises(ValueError, match="iterable"):
        ms.export_recipes(one.id)


def test_transfer_recipe_count_bound_is_atomic(mapping_store, monkeypatch):
    ms = mapping_store
    one = _recipe(ms, "one field")
    two = _recipe(ms, "two field")
    payload = ms.export_recipes()
    events = _event_count(ms)
    monkeypatch.setattr(mappings, "_MAX_TRANSFER_RECIPES", 1)
    for selection in (None, [one.id, two.id]):
        with pytest.raises(ValueError, match="exceeds 1 recipes"):
            ms.export_recipes(selection)
    with pytest.raises(ValueError, match="exceeds 1 recipes"):
        ms.import_recipes(payload)
    assert len(ms.list_all()) == 2
    assert _event_count(ms) == events


def test_transfer_json_size_bound_includes_untrusted_history(mapping_store, monkeypatch):
    ms = mapping_store
    original = _recipe(ms)
    payload = ms.export_recipes([original.id])
    events = _event_count(ms)
    monkeypatch.setattr(mappings, "_MAX_TRANSFER_BYTES", 128)
    with pytest.raises(ValueError, match="exceeds 128 JSON bytes"):
        ms.export_recipes([original.id])
    with pytest.raises(ValueError, match="exceeds 128 JSON bytes"):
        ms.import_recipes(payload)
    assert [r.id for r in ms.list_all()] == [original.id]
    assert _event_count(ms) == events


@pytest.mark.parametrize("change", [
    {"root": "0x1234"},
    {"target_fingerprint": "+" + "a" * 63},
    {"schema_version": 2},
    {"schema_version": True},
    {"revision": 0},
    {"verification_status": "verified"},
    {"steps": [{"op": "add", "offset": 8, "width": 8}]},
    {"history": ["invalid"]},
    {"observations": "invalid"},
    {"name": " "},
    {"evidence_ids": [""]},
    {"pointer.offsets": [8], "pointer": {"offsets": [16]}},
    {"history": [{"resolved_value": float("nan")}]},
])
def test_malformed_second_recipe_leaves_rows_and_audit_untouched(mapping_store, change):
    ms = mapping_store
    original = _recipe(ms)
    payload = ms.export_recipes([original.id])
    payload["recipes"].append({**copy.deepcopy(payload["recipes"][0]), **change})
    baseline = ms.export_recipes()
    events = _event_count(ms)
    with pytest.raises(ValueError):
        ms.import_recipes(payload)
    assert ms.export_recipes() == baseline
    assert _event_count(ms) == events


@pytest.mark.parametrize("payload", [
    [], {}, {"schema_version": 1, "recipes": {}},
    {"schema_version": True, "recipes": []},
    {"schema_version": 2, "recipes": []},
    {"schema_version": 1, "recipes": [None]},
    {"schema_version": 1, "recipes": [], "extra": 1},
])
def test_import_rejects_malformed_envelopes(mapping_store, payload):
    with pytest.raises(ValueError):
        mapping_store.import_recipes(payload)
    assert mapping_store.list_all() == []


@pytest.mark.parametrize("bound_transaction", [False, True])
def test_database_failure_rolls_back_whole_import_and_events(mapping_store, monkeypatch,
                                                           bound_transaction):
    ms = mapping_store
    original = _recipe(ms)
    payload = ms.export_recipes([original.id])
    payload["recipes"].append(copy.deepcopy(payload["recipes"][0]))
    events = _event_count(ms)
    original_put = MappingStore._put
    writes = 0

    def fail_second(self, recipe, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected database failure")
        return original_put(self, recipe, **kwargs)

    monkeypatch.setattr(MappingStore, "_put", fail_second)
    if bound_transaction:
        with ms.store.connection(immediate=True) as db:
            db.execute("INSERT INTO settings(key,value) VALUES('outer','retained')")
            bound = MappingStore(ms.store, connection=db)
            with pytest.raises(RuntimeError, match="injected"):
                bound.import_recipes(payload)
            assert db.execute("SELECT COUNT(*) FROM game_mappings").fetchone()[0] == 1
        with ms.store.connection() as db:
            assert db.execute("SELECT value FROM settings WHERE key='outer'").fetchone()[0] == "retained"
    else:
        with pytest.raises(RuntimeError, match="injected"):
            ms.import_recipes(payload)
    assert [r.id for r in ms.list_all()] == [original.id]
    assert _event_count(ms) == events


def test_import_cannot_forge_migration_identity_or_overwrite_existing_id(mapping_store):
    ms = mapping_store
    legacy = ms.store.put("pointer_candidate", {
        "process_label": "Fixture", "label": "legacy field",
        "base": "mod:Fixture+0x100", "offsets": [8],
    })
    original = _recipe(ms, evidence_ids=[legacy["id"]])
    payload = ms.export_recipes([original.id])
    payload["recipes"][0]["history"] = [{
        "author": "migration", "provenance": {
            "kind": "legacy_migration", "legacy_id": legacy["id"],
        },
    }]
    imported = ms.import_recipes(payload)[0]
    assert imported.id != original.id
    report = migrate_legacy_mappings(ms.store, target_fingerprint_lookup=lambda _: "a" * 64)
    assert not report.errors
    assert report.migrated_ids == [legacy["id"]]
    assert len(ms.list_all()) == 3
    again = migrate_legacy_mappings(ms.store)
    assert again.migrated_ids == []
    assert again.skipped_ids == [legacy["id"]]


@pytest.mark.parametrize("pointer", [False, 0, [], ""])
def test_migration_rejects_malformed_nested_pointer_and_rolls_back(mapping_store, pointer):
    ms = mapping_store
    ms.store.put("pointer_candidate", {"label": "valid", "base": "mod:X+0x0"})
    ms.store.put("pointer_candidate", {"label": "invalid", "pointer": pointer})
    events = _event_count(ms)
    report = migrate_legacy_mappings(ms.store)
    assert report.errors and "pointer must be an object" in report.errors[0]["reason"]
    assert report.migrated_ids == []
    assert ms.list_all() == []
    assert _event_count(ms) == events


def test_migration_markers_survive_revisions_and_rollback_restores_baseline(mapping_store):
    ms = mapping_store
    ms.store.put("pointer_candidate", {"label": "legacy", "base": "mod:X+0x0"})
    report = migrate_legacy_mappings(ms.store)
    ms.revise(ms.list_all()[0].id, name="renamed migration")
    again = migrate_legacy_mappings(ms.store)
    assert len(again.skipped_ids) == 1
    rollback(ms.store, report.backup_path)
    assert MappingStore(ms.store).list_all() == []
    with pytest.raises(ValueError, match="differ"):
        backup_workbench(ms.store, ms.store.path)


def test_equal_clock_ticks_do_not_reuse_mapping_concurrency_tokens(mapping_store, monkeypatch):
    ms = mapping_store
    monkeypatch.setattr(mappings, "now", lambda: "2026-11-16T00:00:00+00:00")
    original = _recipe(ms)
    stale = ms.get(original.id)
    revised = ms.revise(original.id, name="new field")
    assert revised._persisted_at != stale._persisted_at
    with pytest.raises(ValueError, match="concurrently"):
        ms._put(stale, author="test", reason="stale update")
    assert ms.get(original.id).name == "new field"


def test_observe_rejects_a_receipt_if_revision_changed_after_validation(mapping_store):
    ms = mapping_store
    original = _recipe(ms)
    ms.revise(original.id, name="new resolver")
    baseline = ms.export_recipes()
    events = _event_count(ms)
    with pytest.raises(ValueError, match="revision changed"):
        ms.observe(original.id, session_id="session", target_fingerprint="a" * 64,
                   process_instance_id="123:100", resolved_value=42,
                   expected_revision=original.revision)
    assert ms.export_recipes() == baseline
    assert _event_count(ms) == events
    observed = ms.observe(original.id, session_id="session", target_fingerprint="a" * 64,
                          process_instance_id="123:100", resolved_value=43,
                          expected_revision=2)
    assert observed.verification_status == "observed"
    assert observed.observations[-1].recipe_revision == 2


def test_observe_mismatched_target_cannot_invalidate_a_concurrent_revision(mapping_store,
                                                                         monkeypatch):
    ms = mapping_store
    original = _recipe(ms)
    original_get = ms.get

    def revise_after_load(recipe_id):
        loaded = original_get(recipe_id)
        MappingStore(ms.store).revise(recipe_id, name="new resolver")
        return loaded

    monkeypatch.setattr(ms, "get", revise_after_load)
    with pytest.raises(ValueError, match="concurrently"):
        ms.observe(original.id, session_id="session", target_fingerprint="b" * 64,
                   process_instance_id="123:100", resolved_value=42,
                   expected_revision=1)
    saved = original_get(original.id)
    assert saved.revision == 2
    assert saved.verification_status == "candidate"
    assert saved.observations == []
