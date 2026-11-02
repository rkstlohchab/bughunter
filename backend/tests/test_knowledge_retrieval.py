"""Retrieval quality: Porter stemming, acronym/synonym expansion, the expanded
concept vocabulary, and migrating an older FTS table that predates the porter tokenizer."""
import base64

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    service = KnowledgeService(store, start_worker=False)
    monkeypatch.setattr(service, "_embed", lambda texts: (_ for _ in ()).throw(ValueError("no embeddings in test")))
    yield service
    service.shutdown()


def ingest(service, filename, text):
    source = service.import_source(filename=filename, content_base64=base64.b64encode(text.encode()).decode())
    return service.process_source(source["id"])


IDOR = "# Broken object level authorization\n\nAn IDOR happens when the server trusts an object id supplied by the client. Change the order id using a second test account and compare responses."
SSRF = "# Server-side request forgery\n\nSSRF lets an attacker make the server fetch an internal URL. Cloud metadata endpoints are the classic target."
XSS = "# Cross-site scripting\n\nInjecting script into a page that other users view is called XSS. Encode output."


def top_titles(service, query, limit=5):
    return [r["title"] for r in service.search(query, limit)]


def test_porter_stemmer_matches_plurals_and_verb_noun_forms(knowledge):
    ingest(knowledge, "idor.md", IDOR)
    assert "idor" in top_titles(knowledge, "IDORs")[:1]
    ingest(knowledge, "xss.md", XSS)
    assert "xss" in top_titles(knowledge, "injecting scripts")[:1]


def test_synonym_table_bridges_acronyms_porter_cannot(knowledge):
    ingest(knowledge, "idor.md", IDOR)
    assert knowledge.search("BOLA", 5), "BOLA should resolve to IDOR via SYNONYMS"
    assert top_titles(knowledge, "BOLA")[0] == "idor"
    ingest(knowledge, "ssrf.md", SSRF)
    assert top_titles(knowledge, "forging requests")[0] == "ssrf"


def test_query_expansion_keeps_users_own_terms(knowledge):
    ingest(knowledge, "idor.md", IDOR)
    # a literal, unrelated-to-synonyms phrase still has to match on its own words
    assert top_titles(knowledge, "object id supplied by the client")[0] == "idor"


def test_expanded_concepts_cover_ssrf_xss_and_tooling(knowledge):
    ingest(knowledge, "ssrf.md", SSRF)
    ingest(knowledge, "xss.md", XSS)
    ingest(knowledge, "ce.md", "# Cheat Engine\n\nRun a first scan for an exact value, then a next scan to narrow down to a stable pointer.")
    labels = {n["label"] for n in knowledge.graph()["nodes"]}
    assert {"Server-side request forgery", "Injection", "Cheat Engine"} <= labels


def test_old_fts_table_without_porter_is_migrated_from_chunks(tmp_path, monkeypatch):
    """Simulate an install from before the porter tokenizer: real knowledge_chunks/source
    data already indexed into an old-style (non-porter) knowledge_fts. Reopening the
    service (as happens on every app start) must rebuild the FTS table with porter and
    repopulate it from the chunks - the source of truth - not require a re-import."""
    store = Store(tmp_path / "data")
    service = KnowledgeService(store, start_worker=False)
    monkeypatch.setattr(service, "_embed", lambda texts: (_ for _ in ()).throw(ValueError("no embeddings in test")))
    ingest(service, "idor.md", IDOR)
    with store.connection(immediate=True) as db:
        rows = db.execute("SELECT * FROM knowledge_fts").fetchall()
        assert rows, "sanity: the fresh install already indexed the chunk"
        db.execute("DROP TABLE knowledge_fts")
        db.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5(id UNINDEXED, source_id UNINDEXED, text, title, locator UNINDEXED)")
        for r in rows:
            db.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)", (r["id"], r["source_id"], r["text"], r["title"], r["locator"]))
    service.shutdown()

    # Reopening (next app start) must detect the non-porter table and rebuild it from
    # knowledge_chunks, with no re-import needed.
    reopened = KnowledgeService(store, start_worker=False)
    try:
        with store.connection() as db:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'").fetchone()[0]
            assert "porter" in sql.lower()
            row = db.execute("SELECT text,title FROM knowledge_fts WHERE title LIKE 'idor%' LIMIT 1").fetchone()
            assert row["title"].startswith("idor")
        assert top_titles(reopened, "IDORs")[0] == "idor"
        # Running init again (the next start after that) is a no-op: already has porter.
        with store.connection() as db:
            reopened._ensure_fts_table(db)
        assert top_titles(reopened, "IDORs")[0] == "idor"
    finally:
        reopened.shutdown()


def test_section_headings_are_searchable_without_forging_source_text(knowledge):
    # Long section forces the body into a second chunk. The second chunk retains
    # its heading as metadata/search context, not fabricated verbatim evidence.
    material = "# Overview\n\nIntro.\n\n## Helio Protocol\n\n" + ("Packet fields are validated. " * 100) + "\n\n" + ("Each request has a checksum. " * 100)
    ingest(knowledge, "structured.md", material)
    matches = knowledge.search("Helio", limit=10)
    assert len(matches) >= 2
    assert all(m["section"] == "Helio Protocol" for m in matches if "Each request" in m["text"])
    assert any(m["section"] == "Helio Protocol" and "Helio" not in m["text"] for m in matches)
    assert all(m["text"] == m["original_text"] for m in matches)
