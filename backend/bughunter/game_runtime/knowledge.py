"""P01 knowledge service for the game runtime.

Explicit per-session collection binding, task-based retrieval that
does NOT require a source-name match, correction storage/reuse, and
procedure invalidation when a source changes.

The service is a thin facade on the existing ``bughunter.knowledge``
service and the shared :class:`~bughunter.store.Store`. It introduces
three new tables:

* ``game_knowledge_plans`` — per-session binding/plan records.
* ``game_knowledge_corrections`` — authoritative replacement text for a
  cited chunk, with an operator identity and distinguishing ``kind``.
* ``game_knowledge_stale_procedures`` — procedures invalidated because
  their underlying source changed (via a correction or a re-index).

Every citation emitted by :meth:`KnowledgeService.retrieve` carries a
``kind`` field drawn from
``{user_correction, imported_fact, inferred_procedure, executed_result}``
so callers can tell corrected guidance from raw source text, and both
from executed results.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..store import Store, new_id, now


CITATION_KINDS: frozenset[str] = frozenset({
    "user_correction",
    "imported_fact",
    "inferred_procedure",
    "executed_result",
})


@dataclass
class Citation:
    """A knowledge citation returned to the game runtime.

    Every field uses snake_case to match the wire contract. ``kind``
    distinguishes operator corrections from imported source text; a
    superseded chunk is never emitted alongside its correction.
    """
    source_id: str
    chunk_id: str
    locator: str
    text: str
    title: str
    score: float
    kind: str = "imported_fact"
    url: str | None = None
    project_id: str | None = None
    correction_id: str | None = None
    platform_scope: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in CITATION_KINDS:
            raise ValueError(f"unknown citation kind: {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "locator": self.locator,
            "text": self.text,
            "title": self.title,
            "score": self.score,
            "kind": self.kind,
            "url": self.url,
            "project_id": self.project_id,
            "correction_id": self.correction_id,
            "platform_scope": self.platform_scope,
        }
        if self.extras:
            value["extras"] = dict(self.extras)
        return value


class KnowledgeService:
    """Game-runtime knowledge binding, retrieval and correction reuse.

    ``knowledge`` is the existing :class:`bughunter.knowledge.KnowledgeService`
    used for the underlying full-text/semantic search. It may be ``None``
    in tests that only exercise binding/correction bookkeeping.
    """

    def __init__(self, store: Store, knowledge: Any | None = None) -> None:
        self.store = store
        self.knowledge = knowledge
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with self.store.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS game_knowledge_plans (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_ids TEXT NOT NULL,
                    bound_source_ids TEXT NOT NULL,
                    excerpts TEXT,
                    source_locators TEXT,
                    applicability TEXT,
                    proposed_steps TEXT,
                    unsupported_assumptions TEXT,
                    planned_verification TEXT,
                    task_description TEXT,
                    operation_kind TEXT,
                    status TEXT NOT NULL DEFAULT 'bound',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS game_knowledge_plans_session
                    ON game_knowledge_plans(session_id);
                CREATE TABLE IF NOT EXISTS game_knowledge_source_versions (
                    session_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, PRIMARY KEY(session_id, source_id)
                );

                CREATE TABLE IF NOT EXISTS game_knowledge_corrections (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT,
                    source_id TEXT,
                    chunk_id TEXT,
                    correction_text TEXT NOT NULL,
                    superseded_text TEXT,
                    operator_identity TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'user_correction',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS game_knowledge_corrections_source
                    ON game_knowledge_corrections(source_id);
                CREATE INDEX IF NOT EXISTS game_knowledge_corrections_chunk
                    ON game_knowledge_corrections(chunk_id);

                CREATE TABLE IF NOT EXISTS game_knowledge_stale_procedures (
                    source_id TEXT NOT NULL,
                    procedure_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    marked_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, procedure_id)
                );
                """
            )

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------
    def bind_collections(
        self, session_id: str, project_ids: list[str]
    ) -> dict[str, Any]:
        """Persist the explicit knowledge binding for ``session_id``.

        Every source that is either shared (``project_id is None``) or
        belongs to one of ``project_ids`` is enumerated once and stored
        with the plan record, so later retrieval cannot leak material
        from a project the session is not bound to.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(project_ids, list) or any(
            not isinstance(p, str) or not p for p in project_ids
        ):
            raise ValueError("project_ids must be a list[str] of non-empty ids")

        allowed_projects = set(project_ids)
        bound: list[str] = []
        for source in self.store.list("source"):
            pid = source.get("project_id")
            if pid is None or pid in allowed_projects:
                bound.append(source["id"])
        bound_source_ids = sorted(set(bound))

        plan_id = new_id("knowledge_plan")
        stamp = now()
        with self.store.connection(immediate=True) as db:
            db.execute(
                "INSERT INTO game_knowledge_plans("
                "id, session_id, project_ids, bound_source_ids, status,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    plan_id,
                    session_id,
                    json.dumps(sorted(set(project_ids))),
                    json.dumps(bound_source_ids),
                    "bound",
                    stamp,
                    stamp,
                ),
            )
        return {
            "id": plan_id,
            "session_id": session_id,
            "project_ids": sorted(set(project_ids)),
            "bound_source_ids": bound_source_ids,
            "created_at": stamp,
        }

    def _bindings_for(self, session_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM game_knowledge_plans WHERE session_id=? AND status='bound'"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _allowed_scope(
        self, session_id: str
    ) -> tuple[set[str], set[str]]:
        """Return (allowed_source_ids, allowed_project_ids) for a session."""
        allowed_sources: set[str] = set()
        allowed_projects: set[str] = set()
        for row in self._bindings_for(session_id):
            try:
                allowed_sources.update(json.loads(row["bound_source_ids"]))
                allowed_projects.update(json.loads(row["project_ids"]))
            except (TypeError, ValueError):
                continue
        return allowed_sources, allowed_projects

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def retrieve(
        self,
        session_id: str,
        task_description: str,
        operation_kind: str | None = None,
        top_k: int = 5,
    ) -> list[Citation]:
        """Task-based retrieval across every bound source.

        The query is scored on text content across all bound sources,
        not on filename/title tokens, so an operation request can find
        the course even when no lesson title matches the operation
        keyword. Private project isolation is preserved: a source outside
        the bound projects is never returned.

        Active corrections replace their superseded chunks: the operator
        reads the corrected text with ``kind='user_correction'`` and the
        original passage is excluded from the same result set.
        """
        if not isinstance(task_description, str) or not task_description.strip():
            raise ValueError("task_description must be a non-empty string")
        top_k = max(1, min(int(top_k), 20))

        allowed_sources, allowed_projects = self._allowed_scope(session_id)
        allowed_sources = {sid for sid in allowed_sources
                           if (source := self.store.get("source", sid)) is not None
                           and source.get("project_id") in {None, *allowed_projects}}
        if not allowed_sources:
            return []

        query = task_description.strip()
        if operation_kind:
            query = f"{operation_kind} {query}"

        self._refresh_sources(session_id, allowed_sources)
        corrections_by_chunk = {
            c["chunk_id"]: c
            for c in self._active_corrections()
            if c.get("chunk_id") and c.get("source_id") in allowed_sources
        }

        raw: list[dict[str, Any]] = []
        if self.knowledge is not None:
            try:
                for project in sorted(allowed_projects) or [None]:
                    raw.extend(self.knowledge.search(query, limit=top_k * 6, project_id=project))
                raw.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
            except Exception:
                raw = []

        citations: list[Citation] = []
        seen_chunks: set[str] = set()
        for row in raw:
            source_id = row.get("source_id")
            if source_id not in allowed_sources:
                continue
            chunk_id = row.get("id") or row.get("chunk_id")
            if not chunk_id or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            citation_data = row.get("citation") or {}
            source_project_id = self._project_id_for(source_id)

            correction = corrections_by_chunk.get(chunk_id)
            if correction and correction.get("superseded_text") != row.get("text"):
                correction = None
            if correction:
                citations.append(
                    Citation(
                        source_id=source_id,
                        chunk_id=chunk_id,
                        locator=str(row.get("locator") or "0"),
                        text=correction["correction_text"],
                        title=str(row.get("title") or "Resource"),
                        score=float(row.get("score") or 0.0),
                        kind="user_correction",
                        url=citation_data.get("url"),
                        project_id=source_project_id,
                        correction_id=correction["id"],
                        platform_scope=(self.store.get("source", source_id) or {}).get("platform_scope"),
                    )
                )
            else:
                citations.append(
                    Citation(
                        source_id=source_id,
                        chunk_id=chunk_id,
                        locator=str(row.get("locator") or "0"),
                        text=str(row.get("text") or ""),
                        title=str(row.get("title") or "Resource"),
                        score=float(row.get("score") or 0.0),
                        kind="imported_fact",
                        url=citation_data.get("url"),
                        project_id=source_project_id,
                        platform_scope=(self.store.get("source", source_id) or {}).get("platform_scope"),
                    )
                )
            if len(citations) >= top_k:
                break
        return citations

    def _refresh_sources(self, session_id: str, source_ids: set[str]) -> None:
        """Fingerprint all bound active chunks, including passages not in this query."""
        with self.store.connection(immediate=True) as db:
            for source_id in source_ids:
                rows = db.execute("SELECT id,text,locator FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1 ORDER BY id", (source_id,)).fetchall()
                source = self.store.get("source", source_id) or {}
                revision = {"chunks": [tuple(r) for r in rows], "content_hash": source.get("content_hash"),
                            "platform_scope": source.get("platform_scope"), "project_id": source.get("project_id")}
                fingerprint = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
                prior = db.execute("SELECT fingerprint FROM game_knowledge_source_versions WHERE session_id=? AND source_id=?", ("source_revision", source_id)).fetchone()
                if prior and prior["fingerprint"] != fingerprint:
                    self._mark_procedures_stale(db, source_id, "source_changed", now())
                    db.execute("UPDATE game_knowledge_corrections SET active=0 WHERE source_id=? AND NOT EXISTS (SELECT 1 FROM knowledge_chunks c WHERE c.id=game_knowledge_corrections.chunk_id AND c.source_id=? AND c.text=game_knowledge_corrections.superseded_text AND coalesce(json_extract(c.metadata,'$.active'),1)=1)", (source_id, source_id))
                db.execute("INSERT OR REPLACE INTO game_knowledge_source_versions VALUES(?,?,?)", ("source_revision", source_id, fingerprint))

    def prepare_plan(self, session_id: str, task_description: str, *, platform: str,
                     operation_kind: str | None = None) -> dict[str, Any]:
        """Produce a persisted, cited investigation draft for the task runner."""
        citations = self.retrieve(session_id, task_description, operation_kind)
        applicable = [c for c in citations if c.platform_scope in (None, "shared", platform)]
        rejected = [c for c in citations if c not in applicable]
        steps = [{"id": f"consult_{c.chunk_id}", "action": "consult_procedure",
                  "instruction": c.text, "source_id": c.source_id, "chunk_id": c.chunk_id,
                  "kind": c.kind, "correction_id": c.correction_id} for c in applicable]
        gaps = [f"Platform conflict: {c.source_id} is for {c.platform_scope}, target is {platform}" for c in rejected]
        gaps.append("Retrieved passages are bounded evidence; task completeness and target-build applicability remain unverified.")
        if not applicable:
            gaps.append("No applicable course passage retrieved; request evidence before proposing a mutation.")
        pid = self.persist_plan(session_id, [c.to_dict() for c in applicable],
            [{"source_id": c.source_id, "chunk_id": c.chunk_id, "locator": c.locator} for c in applicable],
            {"platform": platform, "rejected_citations": [c.to_dict() for c in rejected]}, steps, gaps,
            {"required": ["validate_target_build", "independent_readback", "observe_behavior"],
             "state": "not_run"}, task_description, operation_kind)
        return self.get_plan(pid)

    def _project_id_for(self, source_id: str) -> str | None:
        if not source_id:
            return None
        row = self.store.get("source", source_id)
        return (row or {}).get("project_id")

    # ------------------------------------------------------------------
    # Plan persistence
    # ------------------------------------------------------------------
    def persist_plan(
        self,
        session_id: str,
        excerpts: list[dict[str, Any]],
        source_locators: list[dict[str, Any]],
        applicability: dict[str, Any],
        proposed_steps: list[dict[str, Any]],
        unsupported_assumptions: list[str],
        planned_verification: dict[str, Any],
        task_description: str | None = None,
        operation_kind: str | None = None,
    ) -> str:
        """Persist the plan the investigation will use and return its id."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        for name, value in (
            ("excerpts", excerpts),
            ("source_locators", source_locators),
            ("proposed_steps", proposed_steps),
            ("unsupported_assumptions", unsupported_assumptions),
        ):
            if not isinstance(value, list):
                raise ValueError(f"{name} must be a list")
        if not isinstance(applicability, dict) or not isinstance(
            planned_verification, dict
        ):
            raise ValueError("applicability and planned_verification must be dicts")

        bindings = self._bindings_for(session_id)
        project_ids: list[str] = []
        bound_source_ids: list[str] = []
        for row in bindings:
            try:
                project_ids.extend(json.loads(row["project_ids"]))
                bound_source_ids.extend(json.loads(row["bound_source_ids"]))
            except (TypeError, ValueError):
                continue
        project_ids = sorted(set(project_ids))
        bound_source_ids = sorted(set(bound_source_ids))

        allowed, projects = self._allowed_scope(session_id)
        with self.store.connection() as db:
            seen = set()
            for locator in source_locators:
                source_id, chunk_id = locator.get("source_id"), locator.get("chunk_id")
                if (source_id, chunk_id) in seen:
                    raise ValueError("duplicate plan citation")
                seen.add((source_id, chunk_id))
                source = self.store.get("source", source_id)
                if source_id not in allowed or not source or source.get("project_id") not in {None, *projects}:
                    raise ValueError("plan citation is outside the current binding")
                chunk = db.execute("SELECT * FROM knowledge_chunks WHERE id=? AND source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1", (chunk_id, source_id)).fetchone()
                if not chunk or str(chunk["locator"]) != str(locator.get("locator")):
                    raise ValueError("plan citation does not resolve to the current source")
                matching = [e for e in excerpts if e.get("chunk_id") == chunk_id and e.get("source_id", source_id) == source_id]
                correction = db.execute("SELECT * FROM game_knowledge_corrections WHERE chunk_id=? AND source_id=? AND superseded_text=? AND active=1 ORDER BY rowid DESC LIMIT 1", (chunk_id, source_id, chunk["text"])).fetchone()
                expected = correction["correction_text"] if correction else chunk["text"]
                expected_kind = "user_correction" if correction else "imported_fact"
                if len(matching) != 1 or matching[0].get("text") != expected or matching[0].get("kind", expected_kind) != expected_kind or matching[0].get("correction_id", correction["id"] if correction else None) != (correction["id"] if correction else None):
                    raise ValueError("plan excerpt must match current cited evidence")
            if len(excerpts) != len(source_locators):
                raise ValueError("every excerpt requires one resolvable locator")

        plan_id = new_id("knowledge_plan")
        stamp = now()
        with self.store.connection(immediate=True) as db:
            db.execute(
                "INSERT INTO game_knowledge_plans("
                "id, session_id, project_ids, bound_source_ids,"
                " excerpts, source_locators, applicability, proposed_steps,"
                " unsupported_assumptions, planned_verification,"
                " task_description, operation_kind,"
                " status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    session_id,
                    json.dumps(project_ids),
                    json.dumps(bound_source_ids),
                    json.dumps(excerpts),
                    json.dumps(source_locators),
                    json.dumps(applicability),
                    json.dumps(proposed_steps),
                    json.dumps(unsupported_assumptions),
                    json.dumps(planned_verification),
                    task_description,
                    operation_kind,
                    "persisted",
                    stamp,
                    stamp,
                ),
            )
        return plan_id

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM game_knowledge_plans WHERE id=?", (plan_id,)
            ).fetchone()
        if not row:
            return None
        plan = dict(row)
        for name in (
            "project_ids",
            "bound_source_ids",
            "excerpts",
            "source_locators",
            "applicability",
            "proposed_steps",
            "unsupported_assumptions",
            "planned_verification",
        ):
            value = plan.get(name)
            if value:
                try:
                    plan[name] = json.loads(value)
                except (TypeError, ValueError):
                    pass
        return plan

    # ------------------------------------------------------------------
    # Corrections
    # ------------------------------------------------------------------
    def apply_correction(
        self,
        plan_id: str,
        correction_text: str,
        operator_identity: str,
        chunk_id: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Store an operator correction for a plan and mark its dependents stale.

        If ``chunk_id``/``source_id`` are not provided, the first entry in
        the plan's ``source_locators`` is used.
        """
        if not isinstance(correction_text, str) or not correction_text.strip():
            raise ValueError("correction_text must be a non-empty string")
        if not isinstance(operator_identity, str) or not operator_identity.strip():
            raise ValueError("operator_identity must be a non-empty string")

        plan = self.get_plan(plan_id)
        if not plan:
            raise ValueError(f"unknown plan_id: {plan_id}")
        locators = plan.get("source_locators") or []
        if not source_id and locators:
            source_id = locators[0].get("source_id")
        if not chunk_id and locators:
            chunk_id = locators[0].get("chunk_id")
        if source_id not in plan.get("bound_source_ids", []) or not any(
                loc.get("source_id") == source_id and loc.get("chunk_id") == chunk_id for loc in locators):
            raise ValueError("correction must refer to a citation in this bound plan")

        allowed, projects = self._allowed_scope(plan["session_id"])
        source = self.store.get("source", source_id)
        if source_id not in allowed or not source or source.get("project_id") not in {None, *projects}:
            raise ValueError("correction citation is outside the current binding")
        if plan.get("status") != "persisted":
            raise ValueError("correction requires a current persisted plan")

        correction_id = new_id("correction")
        stamp = now()
        superseded_text: str | None = None
        with self.store.connection(immediate=True) as db:
            if chunk_id:
                row = db.execute(
                    "SELECT text FROM knowledge_chunks WHERE id=? AND source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1", (chunk_id, source_id)
                ).fetchone()
                if not row:
                    raise ValueError("correction citation no longer resolves")
                excerpt = next((e for e in plan.get("excerpts", []) if e.get("chunk_id") == chunk_id), {})
                expected = excerpt.get("text")
                previous = db.execute("SELECT correction_text FROM game_knowledge_corrections WHERE source_id=? AND chunk_id=? AND superseded_text=? AND active=1 ORDER BY rowid DESC LIMIT 1", (source_id, chunk_id, row["text"])).fetchone()
                if expected != (previous["correction_text"] if previous else row["text"]):
                    raise ValueError("correction citation has changed; prepare a fresh plan")
                superseded_text = row["text"]
            db.execute("UPDATE game_knowledge_corrections SET active=0 WHERE source_id=? AND chunk_id=?", (source_id, chunk_id))
            db.execute(
                "INSERT INTO game_knowledge_corrections("
                "id, plan_id, source_id, chunk_id, correction_text,"
                " superseded_text, operator_identity, kind, active, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    correction_id,
                    plan_id,
                    source_id,
                    chunk_id,
                    correction_text.strip(),
                    superseded_text,
                    operator_identity.strip(),
                    "user_correction",
                    1,
                    stamp,
                ),
            )
            if source_id:
                self._mark_procedures_stale(
                    db, source_id, reason=f"correction:{correction_id}", stamp=stamp
                )
        return {
            "id": correction_id,
            "plan_id": plan_id,
            "source_id": source_id,
            "chunk_id": chunk_id,
            "correction_text": correction_text.strip(),
            "superseded_text": superseded_text,
            "operator_identity": operator_identity.strip(),
            "kind": "user_correction",
            "created_at": stamp,
        }

    def _active_corrections(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM game_knowledge_corrections WHERE active=1"
                " ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_corrections(
        self, source_id: str | None = None, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM game_knowledge_corrections WHERE active=1"
        args: list[Any] = []
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        if plan_id:
            sql += " AND plan_id=?"
            args.append(plan_id)
        sql += " ORDER BY created_at ASC"
        with self.store.connection() as db:
            rows = db.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Procedure invalidation
    # ------------------------------------------------------------------
    def _mark_procedures_stale(
        self, db, source_id: str, reason: str, stamp: str
    ) -> list[str]:
        """Record procedures that depend on ``source_id`` as stale."""
        db.execute("UPDATE game_knowledge_plans SET status='stale', updated_at=? WHERE status='persisted' AND EXISTS (SELECT 1 FROM json_each(game_knowledge_plans.bound_source_ids) WHERE value=?)", (stamp, source_id))
        rows = db.execute(
            "SELECT id FROM entities WHERE kind='course_procedure'"
            " AND json_extract(data,'$.source_id')=?",
            (source_id,),
        ).fetchall()
        affected: list[str] = []
        for row in rows:
            db.execute(
                "INSERT OR REPLACE INTO game_knowledge_stale_procedures("
                "source_id, procedure_id, reason, marked_at) VALUES(?,?,?,?)",
                (source_id, row["id"], reason, stamp),
            )
            affected.append(row["id"])
        return affected

    def invalidate_dependent_procedures(self, source_id: str) -> list[str]:
        """Mark every procedure that depends on ``source_id`` as stale."""
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        stamp = now()
        with self.store.connection(immediate=True) as db:
            affected = self._mark_procedures_stale(
                db, source_id, reason="source_changed", stamp=stamp
            )
        return affected

    def stale_procedures(self, source_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM game_knowledge_stale_procedures"
        args: tuple[Any, ...] = ()
        if source_id:
            sql += " WHERE source_id=?"
            args = (source_id,)
        sql += " ORDER BY marked_at DESC"
        with self.store.connection() as db:
            rows = db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


__all__ = ["KnowledgeService", "Citation", "CITATION_KINDS"]
