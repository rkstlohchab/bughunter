"""P05 — persistent mapping recipes with immutable revisions.

A :class:`MappingStore` owns the lifecycle of saved pointer/value
recipes. Recipes are addressed by ``id``; each successful revision is
appended to ``history`` so prior observations survive edits and nobody
can retroactively claim an old recipe was ``restart_verified``.

States follow the plan:
  * ``candidate`` — operator proposed; no runtime observation yet.
  * ``observed`` — resolver saw the recipe return a usable value in
    at least one live session.
  * ``restart_verified`` — resolver reproduced the recipe across at
    least three fresh processes (see
    :mod:`scripts.verify_game_profile_restarts`).
  * ``stale`` — the target fingerprint changed, a pointer step
    failed, or an operator-marked stale.

Absolute addresses are tied to the original ``process_instance_id``
and must be revalidated before every use; module-relative or static-
field roots can survive restarts, which is why the restart report
must bind to a specific ``target_fingerprint``.
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from ..store import new_id, now
from .contracts import MappingRecipe, PointerStep, validate


_VALID_STATUSES = ("candidate", "observed", "restart_verified", "stale")
_MAX_TRANSFER_RECIPES = 1000
_MAX_TRANSFER_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Observation:
    """One live resolver observation on a recipe."""
    session_id: str
    target_fingerprint: str
    process_instance_id: str
    resolved_value: Any
    resolved_at: str
    recipe_revision: int
    notes: str = ""


@dataclass
class Recipe:
    """Mutable view of a MappingRecipe row + its append-only history."""
    id: str
    name: str
    schema_version: int
    revision: int
    target_fingerprint: str
    value_type: str
    root: str
    steps: list[dict]
    preconditions: list[str]
    evidence_ids: list[str]
    verification_status: str
    history: list[dict] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    _persisted_at: str | None = field(default=None, repr=False)

    def to_contract(self) -> dict:
        model = MappingRecipe(
            id=self.id, schema_version=self.schema_version, revision=self.revision,
            target_fingerprint=self.target_fingerprint, name=self.name,
            value_type=self.value_type, root=self.root,
            steps=[PointerStep(**s) for s in self.steps],
            preconditions=self.preconditions, evidence_ids=self.evidence_ids,
            verification_status=self.verification_status,
        )
        return json.loads(model.model_dump_json())


class MappingStore:
    """Storage API for mapping recipes.

    The shared :class:`~bughunter.store.Store` owns a specialised SQL
    table ``game_mappings`` so recipes do not leak into the generic
    ``entities`` row set (which is used by legacy mapping research).
    """

    _CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS game_mappings (
        id TEXT PRIMARY KEY,
        target_fingerprint TEXT NOT NULL,
        name TEXT NOT NULL,
        value_type TEXT NOT NULL,
        root TEXT NOT NULL,
        steps_json TEXT NOT NULL,
        preconditions_json TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        history_json TEXT NOT NULL,
        observations_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS game_mappings_target ON game_mappings(target_fingerprint);
    CREATE INDEX IF NOT EXISTS game_mappings_status ON game_mappings(verification_status);
    """

    def __init__(self, store, *, connection=None):
        self.store = store
        self._db = connection
        with self._connection() as db:
            for statement in self._CREATE_SQL.split(';'):
                if statement.strip():
                    db.execute(statement)

    def _connection(self):
        return nullcontext(self._db) if self._db is not None else self.store.connection(immediate=True)

    # ------------------------------------------------------------------
    # Construction and revision
    # ------------------------------------------------------------------

    def save_new(self, *, name: str, target_fingerprint: str, value_type: str,
                 root: str, steps: Iterable[dict], preconditions: Iterable[str] = (),
                 evidence_ids: Iterable[str] = ()) -> Recipe:
        """Create a new recipe. Returns the persisted :class:`Recipe`.

        The recipe starts at ``revision=1`` and ``verification_status="candidate"``.
        Later :meth:`observe` / :meth:`mark_restart_verified` / :meth:`invalidate`
        append to history.
        """
        recipe = Recipe(
            id=new_id("mapping"),
            name=self._check_name(name),
            schema_version=1,
            revision=1,
            target_fingerprint=self._check_hex(target_fingerprint),
            value_type=self._check_value_type(value_type),
            root=self._check_root(root),
            steps=[self._check_step(s) for s in steps],
            preconditions=[self._check_text(p) for p in preconditions],
            evidence_ids=[self._check_text(e) for e in evidence_ids],
            verification_status="candidate",
            history=[],
        )
        # Contract-level validation catches schema drift before DB write.
        errors = validate("mapping_recipe", recipe.to_contract())
        if errors:
            raise ValueError(f"MappingRecipe does not validate: {errors[0]}")
        self._put(recipe, author="operator", reason="created")
        return recipe

    def revise(self, recipe_id: str, *, name: Optional[str] = None,
               root: Optional[str] = None, target_fingerprint: Optional[str] = None,
               steps: Optional[Iterable[dict]] = None,
               preconditions: Optional[Iterable[str]] = None,
               evidence_ids: Optional[Iterable[str]] = None,
               author: str = "operator", reason: str = "edit") -> Recipe:
        """Bump the revision and append the previous state to history.

        The previous ``verification_status`` is NOT carried forward — a
        revised recipe reverts to ``candidate`` and must be re-observed.
        This protects the "repeated observations do not become restart
        verification" rule from the plan.
        """
        current = self.get(recipe_id)
        current.history.append(self._historic_entry(current, author, reason))
        if root is not None:
            current.root = self._check_root(root)
        if target_fingerprint is not None:
            current.target_fingerprint = self._check_hex(target_fingerprint)
        if name is not None:
            current.name = self._check_name(name)
        if steps is not None:
            current.steps = [self._check_step(s) for s in steps]
        if preconditions is not None:
            current.preconditions = [self._check_text(p) for p in preconditions]
        if evidence_ids is not None:
            current.evidence_ids = [self._check_text(e) for e in evidence_ids]
        current.revision += 1
        current.verification_status = "candidate"
        current.observations = []
        errors = validate("mapping_recipe", current.to_contract())
        if errors:
            raise ValueError(f"MappingRecipe revision does not validate: {errors[0]}")
        self._put(current, author=author, reason=reason)
        return current

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def observe(self, recipe_id: str, *, session_id: str, target_fingerprint: str,
                process_instance_id: str, resolved_value: Any,
                notes: str = "", expected_revision: int | None = None) -> Recipe:
        """Record a live resolver observation.

        Promotes ``candidate`` → ``observed``. A mismatching target
        fingerprint marks the recipe ``stale`` without promoting status
        — observing on the wrong target does not elevate evidence.
        """
        recipe = self.get(recipe_id)
        if expected_revision is not None and (
            type(expected_revision) is not int or recipe.revision != expected_revision
        ):
            raise ValueError('mapping revision changed; resolve the current revision before observing')
        if target_fingerprint != recipe.target_fingerprint:
            # Keep the same loaded snapshot so _put's concurrency check also
            # protects invalidation from a concurrent recipe revision.
            reason = "invalidated: target_fingerprint_changed"
            recipe.verification_status = "stale"
            recipe.history.append(self._historic_entry(recipe, author="resolver", reason=reason))
            self._put(recipe, author="resolver", reason=reason)
            return recipe
        observation = Observation(
            session_id=session_id,
            target_fingerprint=target_fingerprint,
            process_instance_id=process_instance_id,
            resolved_value=resolved_value,
            resolved_at=now(),
            recipe_revision=recipe.revision,
            notes=notes,
        )
        recipe.observations.append(observation)
        if recipe.verification_status == "candidate":
            recipe.verification_status = "observed"
            recipe.history.append(self._historic_entry(
                recipe, author="resolver",
                reason=f"promoted to observed via session {session_id}",
            ))
        self._put(recipe, author="resolver", reason="observed")
        return recipe

    def mark_restart_verified(self, recipe_id: str, *,
                              observations: list[Observation]) -> Recipe:
        """Promote to ``restart_verified`` after three independent restarts.

        Requires three observations from distinct
        ``process_instance_id`` values on matching target fingerprint.
        Called by :mod:`scripts.verify_game_profile_restarts`.
        """
        recipe = self.get(recipe_id)
        if recipe.verification_status == 'stale':
            raise ValueError('stale recipe must be revised before restart verification')
        if not recipe.root.startswith('mod:'):
            raise ValueError('restart verification requires a module-relative resolver, not a process-bound root')
        if any(o.recipe_revision != recipe.revision for o in observations):
            raise ValueError('restart observations belong to a different recipe revision')
        if any(not o.process_instance_id or not o.session_id or not o.resolved_at for o in observations):
            raise ValueError('restart observations require process, session and time evidence')
        if any(o.resolved_value is None or (isinstance(o.resolved_value, float) and not math.isfinite(o.resolved_value)) for o in observations):
            raise ValueError('restart observations require finite resolved values')
        distinct_pids = {o.process_instance_id for o in observations}
        if len(distinct_pids) < 3:
            raise ValueError(
                "restart verification requires >=3 observations from "
                f"distinct process_instance_id values; got {len(distinct_pids)}"
            )
        if any(o.target_fingerprint != recipe.target_fingerprint for o in observations):
            raise ValueError("restart observations must target the SAME build fingerprint")
        recipe.observations.extend(observations)
        recipe.verification_status = "restart_verified"
        recipe.history.append(self._historic_entry(
            recipe, author="restart-verifier",
            reason=f"restart_verified across {len(distinct_pids)} pids",
        ))
        self._put(recipe, author="restart-verifier", reason="restart_verified")
        return recipe

    def invalidate(self, recipe_id: str, *, reason: str, author: str) -> Recipe:
        """Mark a recipe ``stale``. Does not bump revision."""
        recipe = self.get(recipe_id)
        recipe.verification_status = "stale"
        recipe.history.append(self._historic_entry(
            recipe, author=author, reason=f"invalidated: {reason}",
        ))
        self._put(recipe, author=author, reason=f"invalidated: {reason}")
        return recipe

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, recipe_id: str) -> Recipe:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM game_mappings WHERE id = ?", (recipe_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"mapping {recipe_id!r} not found")
        return self._row_to_recipe(row)

    def list_for_target(self, target_fingerprint: str,
                        status: Optional[str] = None) -> list[Recipe]:
        sql = "SELECT * FROM game_mappings WHERE target_fingerprint = ?"
        args = [target_fingerprint]
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ValueError(f"status must be one of {_VALID_STATUSES}")
            sql += " AND verification_status = ?"
            args.append(status)
        sql += " ORDER BY name"
        with self._connection() as db:
            rows = db.execute(sql, args).fetchall()
        return [self._row_to_recipe(r) for r in rows]

    def list_all(self) -> list[Recipe]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM game_mappings ORDER BY target_fingerprint, name"
            ).fetchall()
        return [self._row_to_recipe(r) for r in rows]

    def export_recipes(self, recipe_ids: Iterable[str] | None = None) -> dict:
        """Export a coherent, JSON-safe snapshot, including untrusted provenance.

        Status and observations are exported for reference only. Importing this
        snapshot cannot establish observations in the destination workbench.
        Transfers are bounded to 1,000 recipes and 8 MiB of JSON.
        """
        ids = None
        if recipe_ids is not None:
            if isinstance(recipe_ids, (str, bytes)):
                raise ValueError("recipe_ids must be an iterable of mapping IDs")
            ids = list(recipe_ids)
            if any(not isinstance(value, str) or not value.strip() for value in ids):
                raise ValueError("recipe_ids must contain non-empty strings")
            ids = list(dict.fromkeys(ids))
            if len(ids) > _MAX_TRANSFER_RECIPES:
                raise ValueError(f"mapping transfer exceeds {_MAX_TRANSFER_RECIPES} recipes")
        with self._connection() as db:
            if ids is None:
                rows = db.execute(
                    "SELECT * FROM game_mappings ORDER BY target_fingerprint, name, id LIMIT ?",
                    (_MAX_TRANSFER_RECIPES + 1,),
                ).fetchall()
                if len(rows) > _MAX_TRANSFER_RECIPES:
                    raise ValueError(f"mapping transfer exceeds {_MAX_TRANSFER_RECIPES} recipes")
            else:
                rows = []
                for recipe_id in ids:
                    row = db.execute(
                        "SELECT * FROM game_mappings WHERE id = ?", (recipe_id,)
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"mapping {recipe_id!r} not found")
                    rows.append(row)
        records = []
        for row in rows:
            recipe = self._row_to_recipe(row)
            records.append({
                **recipe.to_contract(),
                "history": recipe.history,
                "observations": [o.__dict__ for o in recipe.observations],
            })
        return self._json_copy({"schema_version": 1, "recipes": records})

    def import_recipes(self, payload: dict, *,
                       target_fingerprint: str | None = None) -> list[Recipe]:
        """Import all recipes atomically with fresh IDs and no runtime claims.

        Imported history and observations survive only as explicitly unverified
        provenance. Absolute roots always start stale because process identity
        cannot be transferred between workbenches.
        """
        if not isinstance(payload, dict):
            raise ValueError("mapping import must be an object")
        if set(payload) != {"schema_version", "recipes"}:
            raise ValueError("mapping import requires schema_version and recipes only")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("unsupported mapping import schema_version; expected 1")
        if not isinstance(payload["recipes"], list):
            raise ValueError("mapping import recipes must be a list")
        if len(payload["recipes"]) > _MAX_TRANSFER_RECIPES:
            raise ValueError(f"mapping transfer exceeds {_MAX_TRANSFER_RECIPES} recipes")
        # Copy before examining entries so caller-owned data never becomes
        # mutable persisted state; NaN, cycles and non-JSON values are rejected.
        payload = self._json_copy(payload)
        override = self._check_hex(target_fingerprint) if target_fingerprint is not None else None
        staged = []
        for index, source in enumerate(payload["recipes"]):
            try:
                staged.append(self._import_recipe(source, override))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"mapping import recipes[{index}]: {exc}") from exc

        with self._connection() as db:
            # A savepoint also protects callers that supplied an existing
            # transaction and choose to catch an import error and continue it.
            savepoint = new_id("mapping_import")
            db.execute(f"SAVEPOINT {savepoint}")
            try:
                writer = MappingStore(self.store, connection=db)
                for recipe in staged:
                    writer._put(recipe, author="import", reason="imported_unverified")
            except BaseException:
                db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                db.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
        return staged

    def _import_recipe(self, source: dict, fingerprint: str | None) -> Recipe:
        if not isinstance(source, dict):
            raise ValueError("recipe must be an object")
        # Legacy aliases are not part of the transfer schema, but diagnose
        # conflicting representations before rejecting unsupported fields.
        _pointer_offsets(source)
        contract = {key: value for key, value in source.items()
                    if key not in ("history", "observations")}
        errors = validate("mapping_recipe", contract)
        if errors:
            raise ValueError(f"MappingRecipe does not validate: {errors[0]}")
        if contract["schema_version"] != 1:
            raise ValueError("unsupported recipe schema_version; expected 1")
        if not contract["id"].strip():
            raise ValueError("source recipe id must be non-empty")
        for field_name in ("history", "observations"):
            records = source.get(field_name, [])
            if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                raise ValueError(f"{field_name} must be a list of objects")
        source_fingerprint = self._check_hex(contract["target_fingerprint"])
        evidence = [self._check_text(value) for value in contract["evidence_ids"]]
        if contract["id"] not in evidence:
            evidence.append(contract["id"])
        root = self._check_root(contract["root"])
        recipe = Recipe(
            id=new_id("mapping"), name=self._check_name(contract["name"]),
            schema_version=1, revision=1,
            target_fingerprint=fingerprint or source_fingerprint,
            value_type=self._check_value_type(contract["value_type"]), root=root,
            steps=[self._check_step(step) for step in contract["steps"]],
            preconditions=[self._check_text(value) for value in contract["preconditions"]],
            evidence_ids=evidence,
            verification_status="stale" if root.startswith("abs:") else "candidate",
        )
        entry = self._historic_entry(recipe, author="import", reason="imported_unverified")
        entry["provenance"] = {"kind": "unverified_import", "source_recipe": source}
        recipe.history.append(entry)
        return recipe

    @staticmethod
    def _json_copy(value: Any) -> Any:
        def check_keys(item):
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("JSON object keys must be strings")
                for child in item.values():
                    check_keys(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check_keys(child)
        try:
            check_keys(value)
            encoded = json.dumps(value, allow_nan=False)
            if len(encoded.encode("utf-8")) > _MAX_TRANSFER_BYTES:
                raise ValueError(f"mapping transfer exceeds {_MAX_TRANSFER_BYTES} JSON bytes")
            return json.loads(encoded)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"mapping transfer must contain JSON-safe values: {exc}") from exc

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _put(self, recipe: Recipe, *, author: str, reason: str) -> None:
        with self._connection() as db:
            existed = db.execute(
                "SELECT created_at, updated_at FROM game_mappings WHERE id = ?", (recipe.id,)
            ).fetchone()
            if existed and existed['updated_at'] != recipe._persisted_at:
                raise ValueError('mapping changed concurrently; reload before updating')
            created_at = existed["created_at"] if existed else now()
            updated_at = now()
            if existed and updated_at <= existed['updated_at']:
                # updated_at is also the optimistic concurrency token. Clock
                # rollback or equal clock ticks must not reuse an old token.
                updated_at = (
                    datetime.fromisoformat(existed['updated_at']) + timedelta(microseconds=1)
                ).isoformat()
            db.execute(
                """INSERT OR REPLACE INTO game_mappings
                (id, target_fingerprint, name, value_type, root, steps_json,
                 preconditions_json, evidence_ids_json, verification_status,
                 schema_version, revision, history_json, observations_json,
                 created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    recipe.id,
                    recipe.target_fingerprint,
                    recipe.name,
                    recipe.value_type,
                    recipe.root,
                    json.dumps(recipe.steps),
                    json.dumps(recipe.preconditions),
                    json.dumps(recipe.evidence_ids),
                    recipe.verification_status,
                    recipe.schema_version,
                    recipe.revision,
                    json.dumps(recipe.history),
                    json.dumps([o.__dict__ for o in recipe.observations]),
                    created_at,
                    updated_at,
                ),
            )
            recipe._persisted_at = updated_at
            # Persist the audit event in the same transaction.
            event = {
            "id": recipe.id, "revision": recipe.revision,
            "status": recipe.verification_status,
            "author": author, "reason": reason,
            }
            db.execute('INSERT INTO events(type,data,created_at) VALUES(?,?,?)',
                       ('mapping.put', json.dumps(event), now()))

    def _row_to_recipe(self, row) -> Recipe:
        return Recipe(
            id=row["id"],
            name=row["name"],
            schema_version=row["schema_version"],
            revision=row["revision"],
            target_fingerprint=row["target_fingerprint"],
            value_type=row["value_type"],
            root=row["root"],
            steps=json.loads(row["steps_json"]),
            preconditions=json.loads(row["preconditions_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            verification_status=row["verification_status"],
            history=json.loads(row["history_json"]),
            observations=[Observation(**o) for o in json.loads(row["observations_json"])],
            _persisted_at=row['updated_at'],
        )

    @staticmethod
    def _historic_entry(recipe: Recipe, author: str, reason: str) -> dict:
        return {
            "at": now(),
            "revision": recipe.revision,
            "status_at": recipe.verification_status,
            "author": author,
            "reason": reason,
            "recipe": recipe.to_contract(),
            "observations": [o.__dict__ for o in recipe.observations],
        }

    @staticmethod
    def _check_name(name: str) -> str:
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 120:
            raise ValueError("mapping name must be 2..120 characters")
        return name.strip()

    @staticmethod
    def _check_hex(value: str) -> str:
        import re
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("target_fingerprint must be a 64-character sha256 hex string")
        if not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
            raise ValueError("target_fingerprint must be hex")
        return value.lower()

    @staticmethod
    def _check_value_type(value_type: str) -> str:
        allowed = {"u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64", "bytes"}
        if value_type not in allowed:
            raise ValueError(f"value_type must be one of {sorted(allowed)}")
        return value_type

    @staticmethod
    def _check_root(root: str) -> str:
        # Delegate to the shared contract regex: module-relative, absolute,
        # null, placeholder or legitimate zero offset.
        import re
        if not isinstance(root, str) or not re.fullmatch(
            r"(mod:[A-Za-z0-9._-]+\+0x[0-9A-Fa-f]+|abs:0x[0-9A-Fa-f]+|null|placeholder|\+0x0)",
            root,
        ):
            raise ValueError(f"invalid recipe root: {root!r}")
        return root

    @staticmethod
    def _check_step(step: dict) -> dict:
        if not isinstance(step, dict):
            raise ValueError("pointer step must be an object")
        op = step.get("op")
        offset = step.get("offset")
        width = step.get("width")
        if op not in ("add", "deref"):
            raise ValueError("pointer step op must be 'add' or 'deref'")
        if not isinstance(offset, str):
            raise ValueError("pointer step offset must be a string hex literal")
        import re
        if not re.fullmatch(r"[+-]?0x[0-9A-Fa-f]+", offset):
            raise ValueError(f"pointer step offset must match ^[+-]?0x[0-9A-Fa-f]+$, got {offset!r}")
        if width not in (4, 8):
            raise ValueError("pointer step width must be 4 or 8")
        return {"op": op, "offset": offset, "width": width}

    @staticmethod
    def _check_text(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("preconditions/evidence_ids entries must be non-empty strings")
        return value.strip()


def source_snapshot_digest(entries: Iterable[dict]) -> str:
    """Shared helper: deterministic sha256 over ``path\\tsha256`` rows.

    Used by the restart report and the mapping-level provenance so
    reviewers can bind a saved recipe to the exact source revision that
    produced it.
    """
    pairs = sorted(f"{e['path']}\t{e['sha256']}" for e in entries if e.get("path") and e.get("sha256"))
    return hashlib.sha256("\n".join(pairs).encode()).hexdigest()


def _pointer_offsets(value: dict) -> list:
    """Read legacy aliases without silently dropping malformed nested data."""
    nested = value.get("pointer", {})
    if not isinstance(nested, dict):
        raise ValueError("pointer must be an object")
    choices = [offsets for offsets in (
        value.get("offsets"), value.get("pointer.offsets"), nested.get("offsets")
    ) if offsets is not None]
    if any(not isinstance(offsets, list) for offsets in choices):
        raise ValueError("offsets must be a list")
    if choices and any(offsets != choices[0] for offsets in choices[1:]):
        raise ValueError("conflicting flat/nested pointer offsets")
    return choices[0] if choices else []
