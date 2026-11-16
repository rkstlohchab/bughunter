"""P01 knowledge service tests.

Covers P01.retrieval (≥27/30 answerable in top-5), P01.negative_cases
(10 negatives produce gap/qualification), P01.citations (every emitted
citation resolves to a chunk), P01.isolation (private-project scope),
P01.behavior (a controlled fixture shows retrieved instructions change
investigation steps) and P01.correction_reuse.

Tests use a reviewer-authored corpus and reviewer-authored
``expected_passage_hint`` substrings; the service under test does not
choose its own expected passage.
"""
from __future__ import annotations

import json

import pytest

from bughunter.game_runtime.knowledge import (
    CITATION_KINDS,
    Citation,
    KnowledgeService,
)
from bughunter.game_runtime.knowledge_benchmark import (
    ALL_CASES,
    CORPUS,
    ANSWERABLE_CASES,
    BenchmarkCase,
    CorpusLesson,
    NEGATIVE_CASES,
)
from bughunter.knowledge import KnowledgeService as BaseKnowledgeService
from bughunter.store import Store, now


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------

def _install_source(
    base: BaseKnowledgeService, lesson: CorpusLesson, project_id: str | None = None
) -> None:
    """Insert a lesson as a source + chunk + FTS entry, no file I/O."""
    stamp = now()
    base.store.put(
        "source",
        {
            "id": lesson.source_id,
            "title": lesson.title,
            "filename": f"{lesson.source_id}.md",
            "media_type": "text/markdown",
            "content_hash": lesson.source_id,
            "status": "ready",
            "chunk_count": 1,
            "warnings": [],
            "error": None,
            "semantic_index": "pending",
            "project_id": project_id,
            "platform_scope": lesson.platform,
        },
    )
    chunk_id = f"{lesson.source_id}_c0"
    metadata = {"section": lesson.title, "platform_scope": lesson.platform}
    with base.store.connection(immediate=True) as db:
        db.execute(
            "INSERT OR REPLACE INTO knowledge_chunks"
            "(id, source_id, ordinal, text, original_text, locator,"
            " modality, metadata, corrected) VALUES(?,?,?,?,?,?,?,?,0)",
            (
                chunk_id,
                lesson.source_id,
                0,
                lesson.text,
                lesson.text,
                "paragraph-1",
                "text",
                json.dumps(metadata),
            ),
        )
        db.execute("DELETE FROM knowledge_fts WHERE id=?", (chunk_id,))
        db.execute(
            "INSERT INTO knowledge_fts(id, source_id, text, title, locator)"
            " VALUES(?,?,?,?,?)",
            (chunk_id, lesson.source_id, lesson.text, lesson.title, "paragraph-1"),
        )


@pytest.fixture
def services(tmp_path):
    store = Store(tmp_path / "data")
    base = BaseKnowledgeService(store, start_worker=False)
    for lesson in CORPUS:
        _install_source(base, lesson, project_id=None)
    game_knowledge = KnowledgeService(store, knowledge=base)
    yield base, game_knowledge
    base.shutdown()


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------

def _bound(service: KnowledgeService, session_id: str = "s_default") -> str:
    service.bind_collections(session_id, ["proj_default"])
    return session_id


def _hint_present(hint: str, citations: list[Citation]) -> bool:
    if not hint:
        return False
    needle = hint.lower()
    return any(needle in c.text.lower() for c in citations)


# ---------------------------------------------------------------------------
# P01.retrieval
# ---------------------------------------------------------------------------

def test_p01_retrieval_top_five_meets_threshold(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_retrieval")
    hits = 0
    misses: list[str] = []
    for case in ANSWERABLE_CASES:
        citations = game_knowledge.retrieve(session_id, case.query, top_k=5)
        if _hint_present(case.expected_passage_hint, citations):
            hits += 1
        else:
            misses.append(
                f"{case.id}: hint {case.expected_passage_hint!r} not in top-5"
            )
    assert hits >= 27, (
        f"only {hits}/{len(ANSWERABLE_CASES)} answerable cases hit top-5"
        f"; misses={misses}"
    )


# ---------------------------------------------------------------------------
# P01.negative_cases
# ---------------------------------------------------------------------------

def test_p01_negative_missing_returns_no_matching_hint(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_negative")
    for case in NEGATIVE_CASES:
        if case.case_type != "negative-missing":
            continue
        plan = game_knowledge.prepare_plan(session_id, case.query, platform="macos")
        assert plan["unsupported_assumptions"]
        assert plan["planned_verification"]["state"] == "not_run"
        citations = game_knowledge.retrieve(session_id, case.query, top_k=5)
        # A gap looks either like an empty result or like citations whose
        # text does not actually match the off-corpus query terms.
        terms = [t for t in case.query.lower().split() if len(t) > 3]
        matched_terms = any(
            all(t in c.text.lower() for t in terms) for c in citations
        )
        assert not matched_terms, (
            f"{case.id}: off-corpus query should not match all terms; "
            f"got {len(citations)} citations"
        )


def test_p01_negative_platform_conflict_is_flagged(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_platform")
    for case in NEGATIVE_CASES:
        if case.case_type != "negative-platform-conflict":
            continue
        plan = game_knowledge.prepare_plan(session_id, case.query, platform=case.platform_scope)
        assert plan["applicability"]["rejected_citations"], case.id
        assert any("Platform conflict" in gap for gap in plan["unsupported_assumptions"])
        assert all(e["platform_scope"] in (None, "shared", case.platform_scope) for e in plan["excerpts"])


def _source_platform(base: BaseKnowledgeService, source_id: str) -> str | None:
    row = base.store.get("source", source_id)
    return (row or {}).get("platform_scope")


# ---------------------------------------------------------------------------
# P01.citations
# ---------------------------------------------------------------------------

def test_p01_every_citation_resolves_to_a_chunk(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_cite")
    checked = 0
    for case in ANSWERABLE_CASES:
        citations = game_knowledge.retrieve(session_id, case.query, top_k=5)
        for c in citations:
            with _base.store.connection() as db:
                row = db.execute(
                    "SELECT id, source_id, text FROM knowledge_chunks WHERE id=?",
                    (c.chunk_id,),
                ).fetchone()
            assert row is not None, (
                f"{case.id}: citation chunk {c.chunk_id!r} is not resolvable"
            )
            assert row["source_id"] == c.source_id
            assert c.kind in CITATION_KINDS
            checked += 1
    assert checked > 0


# ---------------------------------------------------------------------------
# P01.isolation
# ---------------------------------------------------------------------------

def test_p01_isolation_private_project_is_not_reachable(tmp_path):
    store = Store(tmp_path / "data")
    base = BaseKnowledgeService(store, start_worker=False)
    try:
        # Create a private project and attach a private lesson to it.
        store.put("project", {"id": "proj_private", "name": "Private"})
        private = CorpusLesson(
            "lesson_private_secret",
            "Private Course",
            "shared",
            "The unique private phrase is zephyr_octopus_42 and nothing else.",
        )
        _install_source(base, private, project_id="proj_private")

        # Also install a shared lesson so retrieval has SOMETHING to return.
        shared = CorpusLesson(
            "lesson_shared_intro",
            "Shared",
            "shared",
            "Shared material covers baseline investigation steps only.",
        )
        _install_source(base, shared, project_id=None)

        game_knowledge = KnowledgeService(store, knowledge=base)
        # An unlinked chat binds to a different project id entirely.
        game_knowledge.bind_collections("s_outsider", ["proj_other"])
        citations = game_knowledge.retrieve(
            "s_outsider",
            "unique private phrase zephyr_octopus_42",
            top_k=5,
        )
        for c in citations:
            assert c.source_id != private.source_id, (
                "private course leaked to an unlinked chat"
            )
            assert "zephyr_octopus_42" not in c.text

        # A chat bound to the owning project does see it.
        game_knowledge.bind_collections("s_owner", ["proj_private"])
        owner_cites = game_knowledge.retrieve(
            "s_owner",
            "unique private phrase zephyr_octopus_42",
            top_k=5,
        )
        assert any(c.source_id == private.source_id for c in owner_cites), (
            "bound owner must still reach its own private course"
        )
    finally:
        base.shutdown()


# ---------------------------------------------------------------------------
# P01.behavior
# ---------------------------------------------------------------------------

def test_p01_behavior_retrieved_instructions_change_steps(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_behavior")

    baseline = game_knowledge.prepare_plan(session_id, "quux_nonexistent_783", platform="macos")
    assert baseline["proposed_steps"] == []
    assert baseline["unsupported_assumptions"]
    informed = game_knowledge.prepare_plan(session_id, "remove cooldown on mac", platform="macos")
    assert informed["proposed_steps"] != baseline["proposed_steps"]
    assert any("killTimer" in step["instruction"] for step in informed["proposed_steps"])
    assert informed["planned_verification"]["state"] == "not_run"
    assert game_knowledge.get_plan(informed["id"]) == informed


# ---------------------------------------------------------------------------
# P01.correction_reuse
# ---------------------------------------------------------------------------

def test_p01_correction_reuse_supersedes_original(services):
    _base, game_knowledge = services
    session_id = _bound(game_knowledge, "s_correction")

    # Original retrieval surfaces the cleanup lesson's quoted passage.
    before = game_knowledge.retrieve(
        session_id,
        "safely restore normal cooldown after disabling",
        top_k=5,
    )
    assert _hint_present("stale captured timer", before)
    target = next(c for c in before if "stale captured timer" in c.text)

    # Persist a plan that names the targeted chunk as its locator.
    plan_id = game_knowledge.persist_plan(
        session_id=session_id,
        excerpts=[{"chunk_id": target.chunk_id, "text": target.text}],
        source_locators=[
            {"source_id": target.source_id, "chunk_id": target.chunk_id,
             "locator": target.locator}
        ],
        applicability={"platform": "macos"},
        proposed_steps=[{"id": "s0", "action": "use original cleanup"}],
        unsupported_assumptions=[],
        planned_verification={"check": "readback"},
        task_description="restore cooldown",
        operation_kind="write",
    )

    # Register a fake procedure that depends on the source so we can prove
    # invalidation propagates to it when the correction lands.
    _base.store.put(
        "course_procedure",
        {
            "id": "procedure_cleanup_for_test",
            "source_id": target.source_id,
            "title": "Cleanup from Lesson H",
            "steps": [],
        },
        id="procedure_cleanup_for_test",
    )

    correction_text = (
        "Corrected cleanup: end the override and wait for the next round;"
        " never write a captured countdown value back after elapsed time."
    )
    correction = game_knowledge.apply_correction(
        plan_id=plan_id,
        correction_text=correction_text,
        operator_identity="operator@local",
    )
    assert correction["source_id"] == target.source_id
    assert correction["chunk_id"] == target.chunk_id
    assert correction["superseded_text"] == target.text

    # Dependent procedure is now marked stale with the correction's reason.
    stale = game_knowledge.stale_procedures(source_id=target.source_id)
    assert any(
        row["procedure_id"] == "procedure_cleanup_for_test"
        and row["reason"].startswith("correction:")
        for row in stale
    )

    # Re-retrieve. The chunk's text in knowledge_chunks has not changed, so
    # the service must fold the correction in and emit it as a
    # user_correction citation that excludes the superseded passage.
    after = game_knowledge.retrieve(
        session_id,
        "safely restore normal cooldown after disabling",
        top_k=5,
    )
    corrected = [c for c in after if c.chunk_id == target.chunk_id]
    assert corrected, "the corrected chunk is still returned, with new text"
    assert all(c.kind == "user_correction" for c in corrected)
    assert all(correction_text in c.text for c in corrected)
    # The superseded passage must not reappear under another kind.
    assert not any(
        c.chunk_id == target.chunk_id
        and c.kind != "user_correction"
        and "stale captured timer" in c.text
        for c in after
    ), "the superseded passage is still being emitted"

    # invalidate_dependent_procedures is also callable directly (source change).
    _base.store.put(
        "course_procedure",
        {
            "id": "procedure_second_dependency",
            "source_id": target.source_id,
            "title": "Second dependent",
            "steps": [],
        },
        id="procedure_second_dependency",
    )
    affected = game_knowledge.invalidate_dependent_procedures(target.source_id)
    assert "procedure_second_dependency" in affected


def test_binding_replacement_private_move_and_forged_citations(services):
    base, service = services
    lesson = CorpusLesson("private", "private", "shared", "uniquezircon baseline")
    _install_source(base, lesson, "owner")
    service.bind_collections("scope", ["owner"])
    before = service.prepare_plan("scope", "uniquezircon", platform="windows")
    assert before["excerpts"]
    service.bind_collections("scope", ["other"])
    assert not service.retrieve("scope", "uniquezircon")
    with pytest.raises(ValueError, match="binding"):
        service.apply_correction(before["id"], "corrected", "operator")
    service.bind_collections("scope", ["owner"])
    source = base.store.get("source", "private")
    base.store.put("source", {**source, "project_id": "other"}, id="private")
    assert not service.retrieve("scope", "uniquezircon")
    with pytest.raises(ValueError):
        service.persist_plan("scope", [{"chunk_id": "fake", "text": "forged"}],
                             [{"source_id": "private", "chunk_id": "fake", "locator": "0"}],
                             {}, [], [], {})


def test_refresh_invalidates_unretrieved_source_and_correction(services):
    base, service = services
    sid = _bound(service, "refresh")
    original = service.prepare_plan(sid, "safely restore normal cooldown after disabling", platform="macos")
    target = next(e for e in original["excerpts"] if "stale captured timer" in e["text"])
    service.apply_correction(original["id"], "replacement passage", "operator", target["chunk_id"], target["source_id"])
    corrected = service.prepare_plan(sid, "safely restore normal cooldown after disabling", platform="macos")
    assert any(s["instruction"] == "replacement passage" and s["kind"] == "user_correction" for s in corrected["proposed_steps"])
    with base.store.connection(immediate=True) as db:
        db.execute("UPDATE knowledge_chunks SET text='new revision' WHERE id=?", (target["chunk_id"],))
    service.retrieve(sid, "quux_nonexistent_783")
    assert service.get_plan(corrected["id"])["status"] == "stale"
    assert not service.list_corrections(source_id=target["source_id"])


def test_library_game_context_uses_production_draft(services):
    from bughunter.library_context import LibraryContext
    base, _service = services
    plan, context = LibraryContext(base).game_task_context("remove cooldown on mac", "context", [], "macos")
    assert plan["id"] in context
    assert "killTimer" in context
    assert "no actions executed" in context
    assert plan["status"] == "persisted"


def test_gamelab_production_start_and_followup_context(services, monkeypatch):
    from types import SimpleNamespace
    from bughunter.game_lab import GameLabService
    base, _service = services
    captured = {}
    class Operator:
        def start(self, **kwargs):
            captured["start"] = kwargs
            return {"id": "oprun_test", "status": "awaiting_user"}
        def send(self, **kwargs):
            captured["send"] = kwargs
            return {"status": "awaiting_user"}
    app = SimpleNamespace(store=base.store, knowledge=base, operator=Operator())
    lab = GameLabService(app)
    monkeypatch.setattr(lab, "hardware_check", lambda: {})
    base.store.put("cheat_project", {"id": "game", "target_platform": "macos", "knowledge_project_ids": []}, id="game")
    result = lab.chat_start("game", "remove cooldown on mac")
    assert "killTimer" in captured["start"]["context"]
    link = base.store.get("game_knowledge_session", result["run_id"])
    assert link["plan_id"]
    lab.chat_send(result["run_id"], "course restore normal cooldown")
    assert "COURSE INVESTIGATION DRAFT" in captured["send"]["reference_context"]
    assert captured["send"]["message"] == "course restore normal cooldown"


@pytest.mark.parametrize("case", [c for c in NEGATIVE_CASES if c.case_type == "correction-expected"], ids=lambda c: c.id)
def test_negative_correction_requests_use_persisted_operator_fix(services, case):
    _base, service = services
    sid = _bound(service, case.id)
    original = service.prepare_plan(sid, case.query, platform="macos")
    assert original["excerpts"]
    target = original["excerpts"][0]
    correction = service.apply_correction(original["id"], "operator correction for " + case.id, "reviewer")
    revised = service.prepare_plan(sid, case.query, platform="macos")
    step = next(s for s in revised["proposed_steps"] if s["chunk_id"] == target["chunk_id"])
    assert step["instruction"] == correction["correction_text"]
    assert step["kind"] == "user_correction"
    assert service.get_plan(original["id"])["status"] == "stale"
