"""P05 — one-way migration from legacy ``pointer_candidate`` rows
into :class:`~.mappings.MappingStore`.

Legacy pointer_notebook rows (kind ``pointer_candidate``) carried an
operator-asserted recipe:

    {
      "process_label": "Among Us (test match 2)",
      "label": "cooldown",
      "base": "0x1234...",     # absolute or module-relative string
      "offsets": ["0x8", "0x20"],
      "value_type": "u32",
      "expected_value": 60,
      "project_id": "proj_...",
    }

The migration:

  1. Uses SQLite's backup API to snapshot the whole workbench to a
     parallel file. Rollback uses the same SQLite API.
  2. Walks legacy rows and inserts a MappingRecipe per row, preserving
     the original row id in ``evidence_ids``.
  3. Marks every migrated recipe ``candidate`` and ``stale`` when the
     legacy base was an absolute pointer: process-bound addresses do
     not survive restarts and must be re-observed on the current build.
  4. Repeated migration is idempotent — a locally recorded migration
     provenance entry identifies already migrated rows. Imported evidence
     and imported provenance cannot suppress a real migration.
  5. The caller gets a :class:`MigrationReport` naming every migrated
     id, every skipped id, and the backup path.

**Never** attempts to elevate a repeated observation to
``restart_verified`` — that path requires the restart harness.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store import new_id
from .mappings import MappingStore, _pointer_offsets


@dataclass
class MigrationReport:
    backup_path: str
    migrated_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    marked_stale: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "backup_path": self.backup_path,
            "migrated": len(self.migrated_ids),
            "skipped": len(self.skipped_ids),
            "marked_stale": len(self.marked_stale),
            "errors": len(self.errors),
        }


def backup_workbench(store, destination: Path | None = None) -> Path:
    """SQLite-consistent backup using the built-in backup API.

    Copy-of-file backups are unsafe against a live writer; the backup
    API handles WAL + journal correctly.
    """
    source_path = Path(store.path)
    if destination is None:
        ts = new_id()
        destination = source_path.parent / f"workbench.pre-mapping-migration.{ts}.sqlite3"
    destination = Path(destination)
    if destination.resolve() == source_path.resolve():
        raise ValueError('backup must differ from the live database')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with store.connection(immediate=False) as src:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
        finally:
            dst.close()
    return destination


def rollback(store, backup_path: Path) -> None:
    """Restore the workbench to the pre-migration snapshot.

    Use while migration is stopped; restore through SQLite, including WAL.
    """
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise FileNotFoundError(f"backup not found: {backup_path}")
    if backup_path.resolve() == Path(store.path).resolve():
        raise ValueError('backup must differ from the live database')
    # SQLite backup into the live connection respects WAL readers; file copy does not.
    with sqlite3.connect(f'file:{backup_path.resolve()}?mode=ro', uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('backup integrity check failed')
        with store.connection() as destination:
            source.backup(destination)


def _legacy_to_recipe(
    legacy: dict,
    *,
    target_fingerprint_lookup: Any,
) -> dict:
    """Shape a legacy pointer_candidate row into MappingStore.save_new kwargs."""
    base = str(legacy.get("base") or "")
    offsets = _pointer_offsets(legacy)
    value_type = legacy.get("value_type") or "u32"
    value_type = {'float':'f32', 'double':'f64', 'uint':'u32', 'int':'i32',
                  'uint64':'u64', 'int64':'i64', 'byte':'i8', 'ubyte':'u8'}.get(value_type, value_type)
    label = legacy.get("label") or legacy.get("process_label") or "migrated"
    process_label = legacy.get("process_label") or "unknown"

    # Normalise base into an AddressRepr string.
    if base.startswith("mod:") or base.startswith("abs:") or base in ("null", "placeholder", "+0x0"):
        root = base
    elif base.startswith("0x"):
        root = f"abs:{base.lower()}"
    else:
        root = "placeholder"

    # Each legacy offset becomes a step. Legacy offsets were ordered
    # Cheat-Engine style: for each but the last, deref; the last is a
    # plain add then read. We keep width=8 for the deref steps (common
    # on 64-bit targets) and width = value width for the last add.
    steps: list[dict] = []
    for i, raw in enumerate(offsets):
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            raise ValueError('offset must be an integer or hex string')
        offset = hex(int(raw, 0) if isinstance(raw, str) else raw)
        # Legacy semantics: *(address + offset), not *address + offset.
        steps.append({'op': 'add', 'offset': offset, 'width': 8})
        if i < len(offsets) - 1:
            steps.append({'op': 'deref', 'offset': '+0x0', 'width': 8})
    if not steps:
        steps.append({'op': 'add', 'offset': '+0x0', 'width': 8})

    # Fingerprint: legacy had no build fingerprint, only a process
    # label. Derive a deterministic placeholder from the label so a
    # later observe() against the real build marks the row stale (and
    # the operator re-saves with a measured fingerprint).
    import hashlib
    fp = hashlib.sha256(
        f"legacy:{process_label}:{label}:{base}".encode()
    ).hexdigest()
    if target_fingerprint_lookup is not None:
        try:
            override = target_fingerprint_lookup(process_label)
        except Exception:
            override = None
        if override:
            fp = override

    return {
        "name": f"{process_label}:{label}",
        "target_fingerprint": fp,
        "value_type": value_type,
        "root": root,
        "steps": steps,
        "preconditions": [],
        "evidence_ids": [legacy.get("id", "")],
    }


def migrate_legacy_mappings(
    store,
    *,
    target_fingerprint_lookup: Any = None,
    dry_run: bool = False,
    backup_destination: Path | None = None,
) -> MigrationReport:
    """Migrate legacy ``pointer_candidate`` rows into the mapping store.

    Args:
        store: the shared :class:`~bughunter.store.Store` instance.
        target_fingerprint_lookup: callable ``process_label -> sha256 | None``.
            When present, overrides the deterministic placeholder
            fingerprint so the migrated recipe binds to the measured
            build and can be observed/accepted immediately.
        dry_run: if True, performs validation and returns the report
            without writing any new rows.
        backup_destination: override the backup file location.

    The function is idempotent: rerunning skips rows with a local migration
    provenance entry. Merely mentioning a legacy ID in evidence is not proof
    that the row has been migrated.

    Historical migrations created before local provenance markers existed may
    be duplicated once. Their evidence IDs cannot distinguish migration from
    an independently saved recipe, so they are not accepted as migration proof.
    """
    backup_path = backup_workbench(store, backup_destination) if not dry_run else Path(store.path).with_suffix(".dry-run-skip.sqlite3")
    report = MigrationReport(backup_path=str(backup_path))
    row_id = ''
    class DryRunRollback(Exception):
        pass
    try:
        with store.connection(immediate=True) as db:
            mapping_store = MappingStore(store, connection=db)
            existing = {
                entry['provenance']['legacy_id']
                for recipe in mapping_store.list_all()
                for entry in recipe.history
                if entry.get('author') == 'migration'
                and isinstance(entry.get('provenance'), dict)
                and entry['provenance'].get('kind') == 'legacy_migration'
                and isinstance(entry['provenance'].get('legacy_id'), str)
            }
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM entities WHERE kind='pointer_candidate'")]
            for row in rows:
                row_id = row['id']
                if row_id in existing:
                    report.skipped_ids.append(row_id)
                    continue
                kwargs = _legacy_to_recipe(row, target_fingerprint_lookup=target_fingerprint_lookup)
                recipe = mapping_store.save_new(**kwargs)
                entry = mapping_store._historic_entry(
                    recipe, author='migration', reason='legacy_pointer_candidate',
                )
                entry['provenance'] = {'kind': 'legacy_migration', 'legacy_id': row_id}
                recipe.history.append(entry)
                mapping_store._put(recipe, author='migration', reason='legacy_pointer_candidate')
                if recipe.root.startswith('abs:'):
                    mapping_store.invalidate(recipe.id, reason='migrated_absolute_address_is_process_bound', author='migration')
                    report.marked_stale.append(recipe.id)
                report.migrated_ids.append(row_id)
                existing.add(row_id)
            if dry_run:
                raise DryRunRollback()
    except DryRunRollback:
        pass
    except Exception as exc:
        report.errors.append({'id': row_id, 'reason': str(exc)[:200]})
        report.migrated_ids.clear()
        report.marked_stale.clear()

    return report


__all__ = ["migrate_legacy_mappings", "backup_workbench", "rollback", "MigrationReport"]
