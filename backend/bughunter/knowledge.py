"""Local source ingestion, provenance-aware retrieval and a small knowledge graph.

Resources are data. This module never executes code from a document or transcript.
Heavy ingestion is serialized; a source remains useful through keyword search when
an optional local model or extractor is unavailable.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .store import Store, new_id, now
from .import_formats import (MEDIA_EXTENSIONS, IMAGE_EXTENSIONS, MAX_ARCHIVE_BYTES, MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_DEPTH, archive_members, detect_format, read_text, extract_office, fetch_resource)
from .policy import canonical_url
from .security import redact_text

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".http", ".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".rs", ".js", ".ts", ".tsx", ".java", ".cs", ".sh", ".bat"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | MEDIA_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf", ".epub", ".html", ".htm", ".srt", ".vtt", ".zip", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp"}
MAX_FILE_BYTES = 32 * 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARS = 10_000_000
MEDIA_SAMPLING_NOTE = "Visual timestamps indicate sampled frames/scene changes, not verified clicks; actions can fall between samples."
MEDIA_AUDIO_TAIL_NOTE = "Audio continues after the video stream ends; images are sampled only while the video stream exists."


def extraction_messages(messages):
    """Separate completed-reading limitations from failed extraction steps."""
    messages = list(dict.fromkeys(messages))
    notes = {MEDIA_SAMPLING_NOTE, MEDIA_AUDIO_TAIL_NOTE}
    is_note=lambda message:message in notes or message.startswith(('PDF image reading note: ','Static binary analysis note: '))
    return ([m for m in messages if not is_note(m)],
            [m for m in messages if is_note(m)])

# Transparent lexical links bootstrap navigation. They are explicitly inferred;
# model-backed assertions use enrich() and require a real source quotation.
CONCEPTS = (
    ("vulnerability", "Business logic", r"business[ -]logic|price|coupon|discount|checkout"),
    ("vulnerability", "Access control", r"access control|authorization|\bidor\b|\bbola\b|object[ -]level"),
    ("vulnerability", "Injection", r"\binjection\b|\bsqli\b|\bxss\b|cross.site scripting"),
    ("vulnerability", "Session management", r"\bsession\b|\bcookie\b|authentication"),
    ("vulnerability", "Server-side request forgery", r"\bssrf\b|server.side request forgery|cloud metadata"),
    ("vulnerability", "Cross-site request forgery", r"\bcsrf\b|\bxsrf\b|cross.site request forgery"),
    ("vulnerability", "Insecure deserialization", r"deserializ|\bpickle\b|unmarshal"),
    ("vulnerability", "Server-side template injection", r"\bssti\b|template injection"),
    ("vulnerability", "Path traversal", r"path traversal|directory traversal|\.\./"),
    ("vulnerability", "Open redirect", r"open redirect"),
    ("vulnerability", "Race condition", r"race condition|\btoctou\b|time.of.check|double.spend|double redemption"),
    ("vulnerability", "JWT and token flaws", r"\bjwt\b|json web token|alg.?none|signing (key|secret)"),
    ("vulnerability", "CORS misconfiguration", r"\bcors\b|cross.origin resource sharing"),
    ("vulnerability", "Prototype pollution", r"prototype pollution|__proto__"),
    ("vulnerability", "Rate limiting and abuse", r"rate limit|brute.force|credential stuffing"),
    ("vulnerability", "Remote code execution", r"\brce\b|remote code execution|command injection"),
    ("vulnerability", "File inclusion", r"\blfi\b|\brfi\b|local file inclusion|remote file inclusion"),
    ("vulnerability", "OAuth and SSO flaws", r"\boauth\b|\bsso\b|identity provider|\bsaml\b"),
    ("vulnerability", "Secrets and credential exposure", r"leaked (api )?key|hardcoded (secret|password|credential)|exposed secret"),
    ("technique", "Coupon eligibility testing", r"coupon|discount|minimum.spend"),
    ("technique", "Price integrity testing", r"unit.price|price|currency|cart.total|quantity"),
    ("technique", "Account comparison", r"two accounts|another account|second account|user.a|user.b"),
    ("technique", "Fuzzing", r"\bfuzz(ing|er)?\b"),
    ("technique", "API schema review", r"\bgraphql\b|openapi|swagger|introspection"),
    ("technique", "Binary diffing", r"\bdiff(ing)?\b.{0,20}(binary|patch)|bindiff"),
    ("prerequisite", "Authorized target scope", r"authorized|authorised|in.scope|permission"),
    ("prerequisite", "Test account", r"test account|test user|test identity"),
    ("tool", "Burp Suite", r"\bburp\b|repeater|intruder"),
    ("tool", "Browser", r"browser|playwright|devtools"),
    ("tool", "Nuclei", r"\bnuclei\b"),
    ("tool", "Cheat Engine", r"cheat engine|pointer scan|\baob\b|first scan|next scan"),
    ("tool", "Ghidra", r"\bghidra\b|decompiler|auto.analysis"),
    ("tool", "x64dbg / debugger", r"x64dbg|x32dbg|\bbreakpoint\b|\bdisassembl"),
    ("tool", "Frida", r"\bfrida\b|dynamic instrumentation"),
)


def _digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _text(value) -> str:
    return re.sub(r"\x00", "", str(value or "")).strip()


def _chunks(text: str, size: int = 1800):
    """Keep paragraphs/code blocks intact where possible; never omit long blocks."""
    pieces = re.split(r"\n\s*\n", _text(text))
    current = ""
    for piece in pieces:
        if len(current) + len(piece) + 2 > size and current:
            yield current
            current = ""
        if len(piece) > size * 2:
            if current:
                yield current
                current = ""
            for offset in range(0, len(piece), size):
                yield piece[offset:offset + size]
        else:
            current = f"{current}\n\n{piece}".strip()
    if current:
        yield current


def _sectioned_chunks(text: str):
    """Associate Markdown heading context without changing quotation-backed chunk text.

    The previous chunker dropped headings from later fragments of a long section.
    Keep section labels in metadata/FTS, not in the chunk's verbatim evidence.
    """
    headings = list(re.finditer(r"(?m)^#{1,6}\s+([^\n]+)", text))
    cursor = 0
    heading_idx = 0
    section = ""
    for chunk in _chunks(text):
        # Most chunks are exact substrings. Fall back to the end of the last
        # chunk if whitespace normalisation prevents an exact match.
        position = text.find(chunk, cursor)
        if position < 0:
            position = cursor
        while heading_idx < len(headings) and headings[heading_idx].start() <= position:
            section = headings[heading_idx].group(1).strip()[:160]
            heading_idx += 1
        yield chunk, section
        cursor = min(len(text), position + len(chunk))


def _fts_title(title: str, section: str = "") -> str:
    return f"{title} | {section}" if section else title


def _seconds_label(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Use an HTTPS YouTube video link without credentials or a custom port.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif re.fullmatch(r"/(shorts|embed)/[^/]+/?", parsed.path):
            video_id = parsed.path.strip("/").split("/")[1]
        else:
            video_id = ""
    else:
        raise ValueError("Only individual youtube.com or youtu.be video links are supported.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("The link must identify one YouTube video, not a channel or playlist.")
    return video_id


# Acronym and terminology aliases the FTS porter tokenizer cannot bridge on its own
# (it already handles plurals and common verb/noun suffixes, e.g. injection/injecting,
# authorization/authorize, session/sessions). Expanded into extra OR terms at query time;
# the person's own words are always kept, this only adds more ways to match them.
SYNONYMS = {
    "bola": ("idor", "broken object level authorization", "object level authorization"),
    "idor": ("bola", "broken object level authorization", "object level authorization"),
    "idors": ("bola", "broken object level authorization"),
    "ssrf": ("server-side request forgery", "server side request forgery"),
    "forgery": ("ssrf", "csrf", "forge", "forging", "forged"),
    "forge": ("forgery", "forging", "forged"),
    "forging": ("forgery", "forge", "forged"),
    "forged": ("forgery", "forge", "forging"),
    "xss": ("cross-site scripting", "cross site scripting"),
    "scripting": ("xss",),
    "csrf": ("xsrf", "cross-site request forgery", "cross site request forgery"),
    "xsrf": ("csrf", "cross-site request forgery"),
    "sqli": ("sql injection",),
    "rce": ("remote code execution", "command injection"),
    "lfi": ("local file inclusion",),
    "rfi": ("remote file inclusion",),
    "ssti": ("server-side template injection", "server side template injection"),
    "toctou": ("race condition", "time of check time of use", "double spend"),
    "race": ("toctou", "race condition"),
    "mitm": ("man in the middle",),
    "jwt": ("json web token",),
    "cors": ("cross-origin resource sharing", "cross origin resource sharing"),
    "sso": ("single sign-on", "oauth", "saml"),
    "oauth": ("sso", "identity provider"),
    "saml": ("sso", "identity provider"),
    "rtfm": ("read the documentation",),
    "ida": ("ghidra", "decompiler", "disassembler"),
    "ghidra": ("ida", "decompiler", "disassembler"),
    "aob": ("array of bytes", "byte pattern", "wildcard scan"),
    "pointer scan": ("stable base address", "pointer chain"),
}


def _expand_query(terms: list[str]) -> str:
    """Build an FTS5 MATCH expression, OR-ing in SYNONYMS for terms the porter
    stemmer can't bridge (acronyms, a few verb/noun pairs). The person's own
    words always stay; this only adds more ways for them to match."""
    seen: set[str] = set()
    pieces: list[str] = []
    for term in terms:
        for variant in (term, *SYNONYMS.get(term.lower(), ())):
            key = variant.lower()
            if key in seen:
                continue
            seen.add(key)
            escaped = variant.replace('"', '""')
            pieces.append(f'"{escaped}"')  # quoting a single word is a harmless no-op phrase
            if len(pieces) >= 60:
                break
        if len(pieces) >= 60:
            break
    return " OR ".join(pieces)


class KnowledgeService:
    def __init__(self, store: Store, start_worker: bool = True, media_runtime=None):
        self.store = store
        self.media_runtime = media_runtime
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._start_worker = start_worker
        self._queued: set[str] = set()
        self._queue_lock = threading.Lock()
        self._cancel_generation = 0
        self._processing = threading.local()
        self._local_vision_lock = threading.Lock()
        self._vec_available = False
        self._init_schema()
        from .course_p0 import CourseP0, ProcedureP0
        self.procedures = ProcedureP0(self)
        self.courses = CourseP0(self, start_worker=start_worker)
        from .experiment_p1 import ExperimentP1
        self.experiments = ExperimentP1(self, run_workers=start_worker)
        if start_worker:
            config=media_runtime.config() if media_runtime else {}
            count=2 if config.get('vision_provider') in {'codex_subscription','claude_subscription'} else 1
            self._workers=[threading.Thread(target=self._work,args=(index,),daemon=True,
                name=f'knowledge-ingestion-{index+1}') for index in range(count)]
            self._worker=self._workers[0]
            for worker in self._workers:
                worker.start()
            self.resume_pending()

    def _init_schema(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL, original_text TEXT NOT NULL, locator TEXT NOT NULL,
                    modality TEXT NOT NULL, metadata TEXT NOT NULL, corrected INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS knowledge_chunks_source ON knowledge_chunks(source_id);
                CREATE TABLE IF NOT EXISTS knowledge_vectors (
                    chunk_id TEXT PRIMARY KEY, model TEXT NOT NULL, dimension INTEGER NOT NULL,
                    embedding BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
                    relation TEXT NOT NULL, source_id TEXT NOT NULL, chunk_id TEXT, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS knowledge_edges_source ON knowledge_edges(source_id);
                CREATE TABLE IF NOT EXISTS knowledge_corrections (
                    id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL, previous_text TEXT NOT NULL,
                    replacement_text TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            self._ensure_fts_table(db)
            self._vec_available = self._load_vec(db)

    @staticmethod
    def _ensure_fts_table(db):
        """Create/rebuild knowledge_fts with the Porter stemmer so plurals and common
        verb/noun suffixes match (IDOR/IDORs, injection/injecting, authorize/authorization).
        An install from before this changed keeps its table without 'porter' in its SQL;
        rebuild it in place from knowledge_chunks + the owning source's title, instead of
        requiring a re-import."""
        existing = db.execute("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'").fetchone()
        if existing and "porter" in (existing[0] or "").lower():
            return
        if existing:
            db.execute("DROP TABLE knowledge_fts")
        db.execute("""CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            id UNINDEXED, source_id UNINDEXED, text, title, locator UNINDEXED,
            tokenize='porter unicode61 remove_diacritics 2'
        )""")
        if existing:
            rows = db.execute("""SELECT c.id,c.source_id,c.text,c.locator,
                                         json_extract(e.data,'$.title') AS title,
                                         json_extract(c.metadata,'$.section') AS section
                                  FROM knowledge_chunks c LEFT JOIN entities e
                                    ON e.kind='source' AND e.id=c.source_id
                                  WHERE coalesce(json_extract(c.metadata,'$.active'),1)=1
                                  ORDER BY c.source_id, c.ordinal""").fetchall()
            db.executemany("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)",
                            [(r["id"], r["source_id"], r["text"], _fts_title(r["title"] or "", r["section"] or ""), r["locator"]) for r in rows])

    @staticmethod
    def _load_vec(db) -> bool:
        try:
            import sqlite_vec
            db.enable_load_extension(True)
            try:
                sqlite_vec.load(db)
            finally:
                db.enable_load_extension(False)
            return bool(db.execute("SELECT vec_version()").fetchone())
        except (ImportError, AttributeError, sqlite3.Error):
            return False

    def _settings(self) -> dict:
        value = self.store.setting("settings", {}) or {}
        value.update(self.store.setting("config", {}) or {})
        return value

    def dispatch(self, method: str, params: dict | None = None):
        p = params or {}
        routes = {
            "sources.list": lambda: self.list_sources(p.get("project_id", "*")),
            "sources.import": lambda: self.import_source(**p),
            "sources.get": lambda: self.get_source(p["id"]),
            "sources.models": lambda: self.set_models(p["ids"], p.get("analysis_model")),
            "sources.coverage": lambda: self.source_coverage(p["source_id"]),
            "sources.delete": lambda: self.delete_source(p["id"]),
            "sources.prepare_audio": lambda: self.prepare_audio(p["id"]),
            "sources.retry": lambda: self.retry_source(p["id"]),
            "sources.control": lambda: self.control_source(p['id'],p['operation']),
            "sources.release_original": lambda: self.portability().release_original(**p),
            "knowledge.export": lambda: self.portability().export_bundle(**p),
            "knowledge.reference_pack.import": lambda: self.import_reference_pack(**p),
            "knowledge.examples.save": lambda: self.portability().save_example(**p),
            "knowledge.examples.list": lambda: self.store.list("learning_example"),
            "knowledge.search": lambda: self.search(p.get("query", ""), p.get("limit", 10), project_id=p.get("project_id", "*")),
            "knowledge.graph": lambda: self.graph(p.get("limit", 250), p.get("project_id", "*"), p.get("query", "")),
            "knowledge.learnings": lambda: self.learnings(p.get("limit", 300)),
            "knowledge.reject": lambda: self.reject(p["id"], p.get("reason", "Rejected by operator")),
            "knowledge.correct": lambda: self.correct(p["id"], p["text"]),
            "knowledge.enrich": lambda: self.enrich(p["source_id"], p["assertions"]),
            "knowledge.frames": lambda: self.list_frames(p["source_id"]),
            "knowledge.status": lambda: self.status(),
            "knowledge.integrity": lambda: self.integrity(p.get("project_id", "*")),
            "courses.import": lambda: self.courses.import_archive(p["path"],p.get("project_id"),p.get("analysis_model")),
            "courses.list": lambda: self.courses.list_batches(p.get("project_id","*")),
            "courses.status": lambda: self.courses.status(p["id"]),
            "courses.resume": lambda: self.courses.resume(p["id"]),
            "knowledge.procedures": lambda: self.procedures.list(p.get("source_id"),p.get("project_id","*"),p.get("limit",150)),
            "knowledge.procedures.get": lambda: self.procedures.get(p["id"]),
            "knowledge.procedures.rebuild": lambda: self.rebuild_procedures(p["source_id"]),
            "experiments.check_source": lambda: self.experiments.check_source(p["source_id"],p.get("limit",30)),
            "experiments.insights": lambda: self.experiments.insights(p.get("project_id","*")),
            "experiments.available": lambda: self.experiments.available(p["procedure_id"]),
            "experiments.start": lambda: self.experiments.start(p["procedure_id"],p["recipe"],p.get("approve_lab",False)),
            "experiments.list": lambda: self.experiments.list(p.get("procedure_id"),p.get("project_id","*"),p.get("limit",100)),
            "experiments.get": lambda: self.experiments.get(p["id"]),
            "experiments.stop": lambda: self.experiments.stop(p["id"]),
            "experiments.retry": lambda: self.experiments.retry(p["id"],p.get("approve_lab",False)),
        }
        if method not in routes:
            raise ValueError(f"Unknown knowledge operation: {method}")
        return routes[method]()

    def status(self) -> dict:
        return {"keyword_search": "ready", "sqlite_vec": "ready" if self._vec_available else "unavailable",
                "embedding_model": self._settings().get("embedding_model", "nomic-embed-text"),
                "worker_running": any(worker.is_alive() for worker in self._workers),
                "worker_count": sum(worker.is_alive() for worker in self._workers),
                "queue_size": self._queue.qsize(),
                "extractors": {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "whisper-cli", "tesseract")}}

    def integrity(self, project_id="*") -> dict:
        """Inspect a consistent local snapshot without repairing it or using a model.

        Scope is applied inside every query. Results contain counts, never private
        source titles, passages or identifiers from another project.
        """

        scope = "" if project_id == "*" else " AND (json_extract(data,'$.project_id') IS NULL OR json_extract(data,'$.project_id')=?)"
        params = () if project_id == "*" else (project_id,)
        selected = """WITH selected_sources AS (
            SELECT id,data FROM entities WHERE kind='source'""" + scope + """),
            selected_chunks AS (SELECT c.* FROM knowledge_chunks c
                JOIN selected_sources s ON s.id=c.source_id WHERE coalesce(json_extract(c.metadata,'$.active'),1)=1) """
        checks = [
            ("missing_keyword_entries", "Passages are missing from keyword search.",
             "SELECT count(*) FROM selected_chunks c WHERE NOT EXISTS (SELECT 1 FROM knowledge_fts f WHERE f.id=c.id)"),
            ("stale_keyword_entries", "Keyword entries do not match their current passage or citation.",
             "SELECT count(*) FROM knowledge_fts f JOIN selected_chunks c ON c.id=f.id WHERE f.text<>c.text OR f.source_id<>c.source_id OR f.locator<>c.locator"),
            ("duplicate_keyword_entries", "Passages have duplicate keyword entries.",
             "SELECT count(*) FROM (SELECT f.id FROM knowledge_fts f JOIN selected_chunks c ON c.id=f.id GROUP BY f.id HAVING count(*)>1)"),
            ("source_count_mismatches", "Completed sources have an incorrect saved passage count.",
             "SELECT count(*) FROM selected_sources s WHERE json_extract(s.data,'$.status') IN ('ready','partial') AND coalesce(json_extract(s.data,'$.chunk_count'),0)<>(SELECT count(*) FROM selected_chunks c WHERE c.source_id=s.id)"),
            ("invalid_vectors", "Stored vectors have invalid dimensions or byte lengths.",
             "SELECT count(*) FROM knowledge_vectors v JOIN selected_chunks c ON c.id=v.chunk_id WHERE v.dimension<1 OR v.dimension>8192 OR length(v.embedding)<>v.dimension*4"),
            ("dangling_graph_edges", "Graph links refer to missing passages or nodes.",
             "SELECT count(*) FROM knowledge_edges e JOIN selected_sources s ON s.id=e.source_id LEFT JOIN knowledge_nodes a ON a.id=e.source LEFT JOIN knowledge_nodes b ON b.id=e.target LEFT JOIN knowledge_chunks c ON c.id=e.chunk_id WHERE a.id IS NULL OR b.id IS NULL OR (e.chunk_id IS NOT NULL AND c.id IS NULL)"),
            ("ungrounded_assertions", "Assertions no longer have an exact quote in their cited passage.",
             "SELECT count(*) FROM knowledge_nodes n JOIN selected_sources s ON s.id=json_extract(n.data,'$.source_id') LEFT JOIN knowledge_chunks c ON c.id=json_extract(n.data,'$.chunk_id') AND c.source_id=s.id WHERE n.id LIKE 'assertion_%' AND coalesce(json_extract(n.data,'$.active'),1)=1 AND (c.id IS NULL OR coalesce(json_extract(n.data,'$.quote'),'')='' OR instr(c.text,json_extract(n.data,'$.quote'))=0)"),
        ]
        if project_id == "*":
            checks.extend([
                ("orphan_passages", "Passages refer to deleted sources.",
                 "SELECT count(*) FROM knowledge_chunks c LEFT JOIN selected_sources s ON s.id=c.source_id WHERE s.id IS NULL"),
                ("orphan_keyword_entries", "Keyword entries refer to deleted passages.",
                 "SELECT count(*) FROM knowledge_fts f LEFT JOIN knowledge_chunks c ON c.id=f.id WHERE c.id IS NULL"),
                ("orphan_vectors", "Vectors refer to deleted passages.",
                 "SELECT count(*) FROM knowledge_vectors v LEFT JOIN knowledge_chunks c ON c.id=v.chunk_id WHERE c.id IS NULL"),
            ])
        issues = []
        with self.store.connection() as db:
            db.execute("BEGIN")
            def count(query):
                return db.execute(selected + query, params).fetchone()[0]
            counts = {
                "sources": count("SELECT count(*) FROM selected_sources"),
                "passages": count("SELECT count(*) FROM selected_chunks"),
                "vectors": count("SELECT count(*) FROM knowledge_vectors v JOIN selected_chunks c ON c.id=v.chunk_id"),
                "sources_processing": count("SELECT count(*) FROM selected_sources WHERE json_extract(data,'$.status') IN ('queued','processing')"),
                "sources_partial": count("SELECT count(*) FROM selected_sources WHERE json_extract(data,'$.status')='partial'"),
                "sources_failed": count("SELECT count(*) FROM selected_sources WHERE json_extract(data,'$.status')='error'"),
            }
            for code, message, query in checks:
                affected = count(query)
                if affected:
                    issues.append({"code": code, "count": affected, "message": message})
            # The saved support text is redacted, just like export/model context.
            # Comparing it to raw text would incorrectly reject credential-bearing sources.
            examples = db.execute(selected + """SELECT e.data,c.text FROM entities e
                JOIN selected_sources s ON s.id=json_extract(e.data,'$.source_id')
                LEFT JOIN selected_chunks c ON c.id=json_extract(e.data,'$.chunk_id') AND c.source_id=s.id
                WHERE e.kind='learning_example' AND json_extract(e.data,'$.reviewed')=1""", params).fetchall()
            stale = sum(1 for row in examples if row["text"] is None or
                        (example := json.loads(row["data"])).get("review_stale") or
                        example.get("supporting_text") != redact_text(row["text"]))
            if stale:
                issues.append({"code": "stale_reviewed_examples", "count": stale,
                               "message": "Reviewed examples no longer match their cited passage."})
        return {"status": "needs_attention" if issues else "healthy", "checked_at": now(),
                "scope": "all" if project_id == "*" else "project" if project_id else "shared",
                "counts": counts, "issues": issues, "issue_count": sum(issue["count"] for issue in issues),
                "read_only": True,
                "limitations": "Checks storage, search indexes and citation consistency. It does not verify factual accuracy or complete extraction; partial and failed sources are counted separately."}

    def portability(self):
        from .knowledge_archive import KnowledgeArchive
        return KnowledgeArchive(self)

    def list_sources(self, project_id="*") -> list[dict]:
        sources = self.store.list("source")
        if project_id == "*":
            return sources
        return [s for s in sources if s.get("project_id") in (None, project_id)]

    def get_source(self, id: str) -> dict:
        source = self.store.get("source", id)
        if not source:
            raise ValueError("Source not found.")
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM knowledge_chunks WHERE source_id=? ORDER BY ordinal", (id,)).fetchall()
            correction_rows = db.execute("SELECT c.* FROM knowledge_corrections c JOIN knowledge_chunks k ON c.chunk_id=k.id WHERE k.source_id=? ORDER BY c.created_at", (id,)).fetchall()
        return {**source, "analysis_model":source.get("analysis_model"),
                "effective_analysis_model":self._effective_model(source), "chunks": [self._chunk_record(r, source) for r in rows], "corrections": [dict(r) for r in correction_rows]}

    def _source_config(self, source):
        from .source_models import media_config
        runtime = getattr(self, 'media_runtime', None)
        return media_config(runtime.config() if runtime else {}, source)

    def _effective_model(self, source):
        from .source_models import effective
        return effective(self._source_config(source), source)

    def set_models(self, ids, analysis_model):
        from .source_models import set_models
        return set_models(self, ids, analysis_model)

    def source_coverage(self, source_id):
        from .media_quality import coverage
        return coverage(self, source_id)

    def _source_vision(self, runtime, *args, **kwargs):
        if kwargs['config'].get('vision_provider', 'local') == 'local':
            with self._local_vision_lock:
                return runtime.vision(*args, **kwargs)
        return runtime.vision(*args, **kwargs)

    def _import_youtube_collection(self, url: str, language: str = "en", download_media: bool = False, project_id=None, analysis_model=None) -> dict:
        """Queue a bounded public playlist/channel subset as individual cited video sources.

        A metadata-only inspection avoids automatically downloading large volumes of media.
        yt-dlp can only enumerate publicly reachable pages; private/login-only channels fail.
        """
        from urllib.parse import urlsplit
        import re
        import yt_dlp

        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            raise ValueError("Use a public HTTPS YouTube playlist or channel link.")
        path = parsed.path.rstrip("/")
        is_collection = (path.startswith(("/@", "/channel/", "/c/", "/user/"))
                         or (path == "/playlist" and "list=" in parsed.query))
        if not is_collection:
            raise ValueError("Use an individual YouTube video, a channel or a playlist link.")
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", language):
            raise ValueError("Use a language code such as en or hi.")
        options = {"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False,
                   "playlistend": 50, "quiet": True, "no_warnings": True,
                   "ignoreerrors": True, "socket_timeout": 15, "retries": 1, "cachedir": False}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                listing = downloader.extract_info(url, download=False)
        except Exception as exc:
            raise ValueError("Could not list public YouTube videos; check the link or site restrictions.") from exc
        if not isinstance(listing, dict) or not listing.get("entries"):
            raise ValueError("No public videos found in that YouTube collection.")
        records, seen = [], set()
        for item in listing["entries"]:
            if not isinstance(item, dict):
                continue
            video_id = str(item.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
                continue
            seen.add(video_id)
            records.append(self.import_source(
                url=f"https://www.youtube.com/watch?v={video_id}",
                language=language, download_media=download_media, project_id=project_id, analysis_model=analysis_model))
            if len(records) >= 50:
                break
        if not records:
            raise ValueError("The collection did not expose video IDs for import.")
        return {"id": "batch_youtube_" + _digest(url)[:18],
                "title": str(listing.get("title") or "YouTube collection")[:150],
                "status": "queued", "source_ids": [r["id"] for r in records],
                "sources": records, "count": len(records), "limited_to_first": 50,
                "message": "Queued up to 50 public videos. Import another batch later for larger channels."}

    def import_reference_pack(self, project_id=None):
        from .reference_pack import import_pack
        return import_pack(self, project_id=project_id)

    def import_source(self, path: str | None = None, filename: str | None = None,
                      content_base64: str | None = None, url: str | None = None,
                      download_media: bool = False, language: str = "en", copy_original: bool = True, project_id=None, analysis_model=None, files=None, paths=None, folder=None) -> dict:
        from .source_models import validate
        analysis_model = validate(analysis_model,self.store)
        if self.store.setting("emergency_stop", False):
            raise ValueError("Tasks are stopped. Use Resume tasks before importing sources.")
        if project_id is not None and not self.store.get("project", project_id):
            raise ValueError("Unknown knowledge project.")
        if files is not None or paths is not None or folder is not None:
            if sum(v is not None for v in (files, paths, folder, path, content_base64, url)) != 1:
                raise ValueError('Provide one files, paths, folder, path, upload or URL input.')
            if folder is not None:
                return self.import_source(path=folder, copy_original=copy_original, project_id=project_id, analysis_model=analysis_model)
            members = paths if paths is not None else files
            if not isinstance(members, list) or not 1 <= len(members) <= 500:
                raise ValueError('Import 1–500 files at a time.')
            inputs = []
            for member in members:
                item = {'path':member} if isinstance(member, str) else member
                if not isinstance(item, dict) or not set(item) <= {'path','filename','content_base64','url'}:
                    raise ValueError('Each file needs a path or a filename and base64 contents.')
                inputs.append(item)
            sources = [self.import_source(**item, copy_original=copy_original, project_id=project_id, analysis_model=analysis_model) for item in inputs]
            return {'id':new_id('batch'), 'status':'queued', 'sources':sources, 'source_ids':[s['id'] for s in sources]}
        if sum(v is not None for v in (path, content_base64, url)) != 1:
            raise ValueError("Provide one local path, uploaded file, or website/video URL.")
        if url:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            if host not in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"}:
                url = canonical_url(url)
                key = _digest("web:"+url)
                scoped_key = _digest(key + ":" + project_id) if project_id else key
                id = f"source_{scoped_key[:24]}"
                if existing := self.store.get("source",id):
                    if analysis_model is not None:
                        existing = self.set_models([id], analysis_model)["sources"][0]
                    return {**existing,"duplicate":True}
                source = self.store.put("source",{"id":id,"title":host+(urlparse(url).path or "/"),"url":url,"web_resource":True,
                    "filename":Path(urlparse(url).path).name or "index.html","media_type":"website","content_hash":key,
                    "status":"queued","chunk_count":0,"error":None,"warnings":[],"semantic_index":"pending",
                    "project_id": project_id, "analysis_model":analysis_model})
                self._schedule(id)
                return source
            try:
                video_id = youtube_video_id(url)
            except ValueError:
                return self._import_youtube_collection(url, language, download_media, project_id=project_id, analysis_model=analysis_model)
            if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", language):
                raise ValueError("Use a language code such as en or hi.")
            key = _digest(f"youtube:{video_id}:{language}")
            scoped_key = _digest(key + ":" + project_id) if project_id else key
            id = f"source_{scoped_key[:24]}"
            existing = self.store.get("source", id)
            if existing:
                if analysis_model is not None:
                    existing = self.set_models([id], analysis_model)["sources"][0]
                # An explicit second request can add frames to a transcript-only source.
                if download_media and not existing.get("download_media"):
                    self._update(id, download_media=True)
                    return self.retry_source(id)
                return {**existing, "duplicate": True}
            source = self.store.put("source", {"id": id, "title": f"YouTube lesson {video_id}",
                "filename": f"{video_id}.youtube", "media_type": "video/youtube", "content_hash": key,
                "url": f"https://www.youtube.com/watch?v={video_id}", "video_id": video_id,
                "language": language, "download_media": bool(download_media), "status": "queued",
                "chunk_count": 0, "error": None, "warnings": [], "semantic_index": "pending",
                "project_id": project_id, "analysis_model":analysis_model})
            self._schedule(id)
            return source
        original: Path | None = None
        if path is not None:
            original = Path(path).expanduser().resolve()
            if original.is_dir():
                candidates = [p for p in sorted(original.rglob("*")) if p.is_file() and not p.is_symlink()
                              and not any(part.startswith(".") for part in p.relative_to(original).parts)]
                if len(candidates) > 500:
                    raise ValueError("Import at most 500 course files at a time.")
                if not candidates:
                    raise ValueError("The folder contains no supported documents or media.")
                sources = [self.import_source(path=str(p), copy_original=copy_original, project_id=project_id, analysis_model=analysis_model) for p in candidates]
                return {"id": new_id("batch"), "title": original.name, "status": "queued",
                        "source_ids": [s["id"] for s in sources], "sources": sources}
            if not original.is_file():
                raise ValueError("Choose an existing file or course folder.")
            if original.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Local resources must be smaller than 32 GiB. Unpack large course archives into a folder first.")
            filename = original.name
            hasher = hashlib.sha256()
            with original.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            key = hasher.hexdigest()
            raw = None
        else:
            if not filename or Path(filename).name != filename:
                raise ValueError("An upload needs a simple filename.")
            if len(content_base64 or "") > MAX_UPLOAD_BYTES * 4 // 3 + 8:
                raise ValueError("Uploads are limited to 64 MiB; select a local path for larger files.")
            try:
                raw = base64.b64decode(content_base64 or "", validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("Invalid base64 file contents.") from exc
            key = _digest(raw)
        extension = Path(filename or "").suffix.lower()
        # Keep unknown files as durable sources too. The worker detects their
        # actual format and reports an unsupported parser without executing them.
        scoped_key = _digest(key + ":" + project_id) if project_id else key
        id = f"source_{scoped_key[:24]}"
        existing = self.store.get("source", id)
        if existing and existing.get("original_retained") is not False:
            if analysis_model is not None:
                existing = self.set_models([id], analysis_model)["sources"][0]
            return {**existing, "duplicate": True}
        destination = self.store.root / "sources" / f"{scoped_key}{extension}"
        if original and not copy_original:
            destination = original
        elif original:
            shutil.copyfile(original, destination)
        else:
            destination.write_bytes(raw or b"")
        if not original or copy_original:
            destination.chmod(0o600)
        source = self.store.put("source", {"id": id, "title": Path(filename or "Resource").stem,
            "filename": filename, "media_type": mimetypes.guess_type(filename or "")[0] or "text/plain",
            "content_hash": key, "path": str(destination), "status": "queued", "chunk_count": 0,
            "source_bytes": destination.stat().st_size, "original_retained": True,
            "source_storage": "linked" if original and not copy_original else "copied",
            "warnings": [], "error": None, "semantic_index": "pending", "project_id": project_id, "analysis_model":analysis_model})
        if existing:
            (self.store.root/"artifacts"/id/"extracted.json").unlink(missing_ok=True)
        self._schedule(id)
        self.store.event("source.queued", {"source_id": id})
        return source

    def _ensure_model_worker(self, source):
        from .source_models import HOSTED
        if not self._start_worker or self._source_config(source).get('vision_provider') not in HOSTED:
            return
        with self._queue_lock:
            if len(self._workers) < 2:
                worker = threading.Thread(target=self._work, args=(1,), daemon=True, name='knowledge-ingestion-2')
                self._workers.append(worker)
                worker.start()

    def _schedule(self, id: str):
        self._ensure_model_worker(self.store.get("source", id) or {})
        with self._queue_lock:
            if id not in self._queued:
                self._queued.add(id)
                self._queue.put(id)

    def _update(self, id: str, **changes) -> dict | None:
        progress=changes.get('media_progress')
        if progress and progress.get('phase')=='frames' and getattr(self._processing,'source_id',None)==id:
            progress['saved_analyzed']=len(getattr(self._processing,'saved_visual_times',set()))
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (id,)).fetchone()
            if not row:
                return None
            source = {**json.loads(row[0]), **changes, "updated_at": now()}
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=? AND kind='source'", (json.dumps(source), source["updated_at"], id))
            return source

    def resume_pending(self):
        from .media_retry import retry_due
        for source in self.list_sources():
            if source.get("status") == "queued" and source.get('media_control') not in {'pause','cancel'} and retry_due(source,cancel_check=lambda id=source['id']:self._check_cancelled(id)):
                self._schedule(source["id"])
            elif source.get("status") == "processing" and not self._pid_alive(source.get("worker_pid")):
                self._update(source["id"], status="queued", resumed=True)
                self._schedule(source["id"])

    @staticmethod
    def _pid_alive(pid) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def cancel_pending(self):
        self._cancel_generation += 1
        for source in self.store.list('source'):
            if source.get('status') in {'queued', 'processing'}:
                self._update(source['id'], status='cancelled', stage='cancelled', error='Stopped by operator. Retry this source explicitly to continue.')

    def _check_cancelled(self, id):
        while True:
            source = self.store.get('source', id)
            if self._stop.is_set() or self.store.setting('emergency_stop', False) or not source or source.get('status') == 'cancelled' or source.get('media_control') == 'cancel':
                raise InterruptedError('Source processing stopped by operator.')
            if source.get('media_control')!='pause':return
            self._stop.wait(.15)

    @staticmethod
    def _retain_source_failure(db,source):
        if source.get('error') and (source.get('stage')=='failed' or source.get('status') in {'error','failed'}):
            source['last_failure']={'error':source['error'],'stage':source.get('stage'),'at':source.get('updated_at')}
        elif source.get('stage')=='failed' and not source.get('last_failure'):
            # Older retries cleared error without retaining its cause. Recover
            # the source's own append-only event before resetting that stage.
            row=db.execute("SELECT data,created_at FROM events WHERE type='source.failed' AND json_extract(data,'$.source_id')=? ORDER BY seq DESC LIMIT 1",(source['id'],)).fetchone()
            if row:
                source['last_failure']={'error':json.loads(row['data']).get('error'),'stage':'failed','at':row['created_at']}

    def control_source(self,id,operation,*,expected_updated_at=None,expected_generation=None):
        self.store.require_desktop_owner()
        if operation not in {'pause','resume','cancel'}:raise ValueError('Choose Pause, Continue or Cancel.')
        if operation=='resume' and self.store.setting('emergency_stop',False):raise ValueError('Use Resume tasks in the top bar before continuing files.')
        with self.store.connection(immediate=True) as db:
            row=db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(id,)).fetchone()
            if not row:raise ValueError('Source not found.')
            source=json.loads(row[0]);status=source['status']
            if expected_updated_at is not None and source.get('updated_at')!=expected_updated_at:
                raise ValueError('Source changed after the resume request; its newer control is preserved.')
            if operation=='resume' and (self.store.setting('emergency_stop',False) or expected_generation is not None and self.store.setting('stop_generation',0)!=expected_generation):
                raise ValueError('Task controls changed after the resume request; the newer stop is preserved.')
            if operation=='pause' and status not in {'queued','processing'}:raise ValueError('Only queued or running files can be paused.')
            if operation=='resume' and status not in {'queued','processing'}:
                retry=True
            else:
                retry=False;source['media_control']=operation
                if operation=='cancel':source.update(status='cancelled',stage='cancelled',provider_wait=None)
                if operation=='resume' and status=='queued':
                    self._retain_source_failure(db,source)
                    source.update(stage='provider_wait' if source.get('provider_wait') else 'queued',error=None,worker_pid=None)
                source['updated_at']=now();db.execute('UPDATE entities SET data=?,updated_at=? WHERE id=?',(json.dumps(source),now(),id))
        if retry:return self.retry_source(id)
        if operation=='resume':self._schedule(id)
        return self.get_source(id)

    def _claim(self, id: str) -> dict | None:
        from .media_retry import retry_due
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE id=? AND kind='source'", (id,)).fetchone()
            if not row:
                return None
            source = json.loads(row[0])
            if (source["status"] != "queued" or self.store.setting("emergency_stop", False)
                    or source.get('media_control') in {'pause','cancel'} or not retry_due(source)):
                return None
            if source.get('provider_wait'):
                source['last_provider_wait']=source.pop('provider_wait')
                progress=source.get('media_progress',{})
                source['media_progress']={**progress,'phase':progress.get('resume_phase','frames')}
            source.update(status="processing", stage="extracting", error=None, worker_pid=os.getpid(), updated_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?", (json.dumps(source), source["updated_at"], id))
            return source

    def _work(self, lane=0):
        self._processing.lane=lane
        failed_source=None
        while not self._stop.is_set():
            if failed_source:
                try:
                    with self.store.connection(immediate=True) as db:
                        row=db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(failed_source[0],)).fetchone()
                        source=json.loads(row[0]) if row else None
                        if source and source.get('status')=='processing' and source.get('worker_pid')==os.getpid():
                            source.update(status='error',stage='failed',worker_pid=None,error=failed_source[1],updated_at=now())
                            source['last_failure']={'error':failed_source[1],'stage':'failed','at':source['updated_at']}
                            db.execute('UPDATE entities SET data=?,updated_at=? WHERE id=?',(json.dumps(source),source['updated_at'],source['id']))
                    failed_source=None
                except Exception:
                    self._stop.wait(5)
                    continue
            try:
                id = self._queue.get(timeout=0.5)
            except queue.Empty:
                try:self.resume_pending()
                except Exception:self._stop.wait(5)
                continue
            if id is None:
                break
            try:
                self.process_source(id)
            except Exception as error:
                # Even recording a failed extraction can fail when storage is
                # full. Keep the lane alive and preserve cancellation controls.
                failed_source=(id,str(error)[:1500])
                self._stop.wait(1)
            finally:
                with self._queue_lock:
                    self._queued.discard(id)
                self._queue.task_done()

    def prepare_audio(self, id):
        """Synchronously publish speech checkpoints, leaving visual processing pending."""
        source = self.store.get('source', id)
        if not source or not source.get('path'):
            raise ValueError('Select an existing local media source.')
        if source.get('status') == 'processing' or (source.get('status') == 'cancelled' and self._pid_alive(source.get('worker_pid'))):
            raise ValueError('Source is already processing; wait or stop its worker first.')
        if self.store.setting('emergency_stop',False):
            raise InterruptedError('Global stop is active.')
        self._update(id,status='queued',media_control='run')
        source = self._claim(id)
        if not source: raise ValueError('Source could not be claimed for audio preparation.')
        self._processing.generation = self._cancel_generation
        self._processing.source_id = id
        try:
            root = self.store.root / 'artifacts' / id
            segments, warnings = self._extract_media(Path(source['path']), root, source, audio_only=True)
            return self._update(id,status='partial',stage='audio_partial' if warnings else 'audio_complete',worker_pid=None,
                audio_prepared=not warnings,warnings=warnings, error=None)
        except InterruptedError as exc:
            return self._update(id,status='cancelled',stage='cancelled',worker_pid=None,error=str(exc))
        except Exception as exc:
            return self._update(id,status='error',stage='failed',worker_pid=None,error=str(exc)[:1000])

    def process_source(self, id: str) -> dict | None:
        """Process a queued source synchronously (also useful for deterministic tests)."""
        source = self._claim(id)
        if not source:
            return self.store.get("source", id)
        self._processing.generation = self._cancel_generation
        self._processing.source_id = id
        try:
            artifact_dir = self.store.root / "artifacts" / id
            artifact_dir.mkdir(exist_ok=True, mode=0o700)
            checkpoint = artifact_dir / "extracted.json"
            if checkpoint.exists():
                extracted = json.loads(checkpoint.read_text())
                segments, warnings = extracted["segments"], extracted["warnings"]
            else:
                segments, warnings = self._extract(source, artifact_dir)
                if sum(len(s.get("text", "")) for s in segments) > MAX_EXTRACTED_CHARS:
                    raise ValueError("Extracted text is too large; split this resource before importing.")
                temporary = checkpoint.with_suffix(".tmp")
                temporary.write_text(json.dumps({"segments": segments, "warnings": warnings}, ensure_ascii=False))
                temporary.replace(checkpoint)
            self._check_cancelled(id)
            source = self._update(id, stage="indexing")
            if not source:
                return None
            count = self._index(source, segments, preserve_existing=(artifact_dir / 'media_timeline.json').exists())
            if count:
                try:
                    self.procedures.build(id)
                except Exception as exc:  # Procedure candidates cannot invalidate the source.
                    warnings.append(f"Procedure extraction unavailable: {str(exc)[:160]}")
            if count == 0:
                warnings.append("No searchable text was extracted. Check extractor/model availability and retry.")
            semantic_status = "unavailable"
            semantic_error = None
            self._check_cancelled(id)
            if count:
                try:
                    semantic_status = self._index_vectors(id)
                except Exception as exc:  # noqa: BLE001 - optional model failures must preserve keyword search
                    semantic_error = str(exc)[:350]
            self._check_cancelled(id)
            warnings, notes = extraction_messages(warnings)
            result = self._update(id, status="partial" if warnings else "ready", stage="complete",
                                 extraction_notes=notes,
                                 warnings=list(dict.fromkeys(warnings)), chunk_count=count,
                                 semantic_index=semantic_status, semantic_error=semantic_error, worker_pid=None)
            self.store.event("source.indexed", {"source_id": id, "chunk_count": count,
                                                "status": result["status"] if result else "deleted"})
            return result
        except InterruptedError as exc:
            latest=self.store.get('source',id) or {}
            if self._stop.is_set() and not self.store.setting('emergency_stop',False) and latest.get('media_control') not in {'pause','cancel'}:
                return self._update(id,status='queued',stage='awaiting_restart',error=None,worker_pid=None)
            return self._update(id, status="cancelled", stage="cancelled", error=str(exc), worker_pid=None)
        except Exception as exc:  # noqa: BLE001 - durable worker boundary records extractor failures
            from .media_retry import subscription_retry
            runtime=getattr(self,'media_runtime',None)
            config=self._source_config(self.store.get("source", id) or source)
            from .media_routing import VisualProviderFailure
            if isinstance(exc,VisualProviderFailure):
                config={**config,'vision_provider':exc.provider,
                    ('claude_vision_model' if exc.provider=='claude_subscription' else 'codex_vision_model'):exc.model}
            try:
                self._check_cancelled(id)
                retry=subscription_retry(exc,config,source.get('provider_retry_attempts',0),
                    cancel_check=lambda:self._check_cancelled(id))
                if retry:
                    return self._defer_provider(id,retry)
            except InterruptedError as stopped:
                return self._update(id,status='cancelled',stage='cancelled',error=str(stopped),worker_pid=None)
            result = self._update(id, status="error", stage="failed", error=str(exc)[:1000], worker_pid=None)
            self.store.event("source.failed", {"source_id": id, "error": str(exc)[:400]})
            return result
        finally:
            # A user may delete a source while an extractor is still running. Do
            # not leave late-created frames/checkpoints behind or recreate it.
            if self.store.get("source", id) is None:
                shutil.rmtree(self.store.root / "artifacts" / id, ignore_errors=True)

    def _defer_provider(self, id, retry):
        with self.store.connection(immediate=True) as db:
            row=db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(id,)).fetchone()
            if not row:
                return None
            source=json.loads(row[0])
            if (self._stop.is_set() or self.store.setting('emergency_stop',False)
                    or source.get('status')=='cancelled' or source.get('media_control')=='cancel'):
                source.update(status='cancelled',stage='cancelled',worker_pid=None)
            else:
                progress=source.get('media_progress',{})
                source.update(status='queued',stage='provider_wait',worker_pid=None,
                    error=None,provider_wait=retry,provider_retry_attempts=retry['attempt'],
                    media_progress={**progress,'phase':'provider_wait',
                        'resume_phase':progress.get('resume_phase',progress.get('phase','frames')),
                        'retry_at':retry['retry_at'],'retry_provider':'Claude' if retry['provider']=='claude_subscription' else 'ChatGPT'})
            source['updated_at']=now()
            db.execute('UPDATE entities SET data=?,updated_at=? WHERE id=?',
                (json.dumps(source),source['updated_at'],id))
        self.store.event('source.provider_wait',{'source_id':id,'retry':retry})
        return source

    def rebuild_procedures(self, id: str) -> dict:
        """Repair derived procedures without rereading or re-extracting originals."""
        source = self.store.get('source', id)
        if not source or source.get('status') not in {'ready', 'partial'}:
            raise ValueError('Wait for source extraction to finish before rebuilding procedures.')
        result = self.procedures.build(id)
        with self.store.connection(immediate=True) as db:
            raw = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (id,)).fetchone()
            current = json.loads(raw[0]) if raw else None
            if current and current.get('status') in {'ready', 'partial'}:
                warnings, notes = extraction_messages([w for w in current.get('warnings', [])
                    if not w.startswith('Procedure extraction unavailable:')])
                if not current.get('chunk_count'):
                    warnings.append('No searchable text was extracted. Check extractor/model availability and retry.')
                current.update(warnings=list(dict.fromkeys(warnings)),
                    extraction_notes=list(dict.fromkeys([*current.get('extraction_notes', []), *notes])),
                    status='partial' if warnings else 'ready', updated_at=now())
                db.execute("UPDATE entities SET data=?,updated_at=? WHERE kind='source' AND id=?",
                    (json.dumps(current), current['updated_at'], id))
        return {**result, 'source_status': current.get('status') if current else 'deleted'}

    def retry_source(self, id: str) -> dict:
        if self.store.setting("emergency_stop", False):
            raise ValueError("Tasks are stopped. Use Resume tasks before retrying sources.")
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(id,)).fetchone()
            if not row: raise ValueError("Source not found.")
            source = json.loads(row[0])
            if source.get("original_retained") is False and not source.get("url"):
                raise ValueError("The original was released. Import the original file again to re-extract it; existing knowledge is still available.")
            if source["status"] in {"processing", "cancelled"} and self._pid_alive(source.get("worker_pid")):
                raise ValueError("This source is still processing.")
            progress=source.get('media_progress',{})
            if progress.get('phase')=='provider_wait':
                source['media_progress']={**progress,'phase':progress.get('resume_phase','frames')}
            self._retain_source_failure(db,source)
            source.update(status="queued",stage="queued",error=None,worker_pid=None,original_retained=True,media_control="resume",
                          provider_wait=None,provider_retry_attempts=0,updated_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?",(json.dumps(source),source['updated_at'],id))
        # Explicit retry re-extracts so newly installed OCR/media tools are used.
        checkpoint = self.store.root / "artifacts" / id / "extracted.json"
        checkpoint.unlink(missing_ok=True)
        self._schedule(id)
        return source

    def wait_for_source(self, id: str, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            source = self.store.get("source", id)
            if source and source["status"] not in {"queued", "processing"}:
                return source
            time.sleep(0.03)
        raise TimeoutError(f"Source {id} is still processing.")

    def shutdown(self):
        self._stop.set()
        for worker in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=2)

    def _extract(self, source: dict, artifacts: Path, budget=None, depth=0) -> tuple[list[dict], list[str]]:
        self._check_cancelled(source["id"])
        if source.get("video_id"):
            return self._extract_youtube(source, artifacts)
        if source.get("web_resource"):
            return self._extract_website(source, artifacts)
        path = Path(source["path"])
        extension = detect_format(path,source.get("filename"))
        if not source.get("archive_member"):
            self._update(source["id"],detected_format=extension.lstrip("."))
        if extension in TEXT_EXTENSIONS or extension == ".text":
            return [{"text": read_text(path), "locator": "Document", "modality": "text"}], []
        if extension in {".html", ".htm"}:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(read_text(path), "html.parser")
            scripted = bool(soup.find("script"))
            if source.get("downloaded_url") and not source.get("archive_member") and soup.title and soup.title.get_text(strip=True):
                source["title"] = soup.title.get_text(strip=True)[:300]
                self._update(source["id"],title=source["title"])
            for node in soup(["script", "style", "noscript", "iframe", "template"]):
                node.decompose()
            text = (soup.find("main") or soup.find("article") or soup.body or soup).get_text("\n", strip=True)
            warnings = ["This page has little readable HTML. JavaScript, logins, and embedded pages were not loaded; import a local export if content is missing."] if source.get("downloaded_url") and scripted and len(text)<200 else []
            return [{"text": text, "locator": "Web page" if source.get("downloaded_url") else "Document", "modality": "text"}], warnings
        if extension == ".zip": return self._extract_archive(source,artifacts,budget,depth)
        if extension in {".docx", ".pptx", ".xlsx", ".odt"}: return extract_office(path,extension)
        if extension == ".image": return self._extract_image(source,artifacts)
        if extension == ".pdf":
            return self._extract_pdf(path,source_id=source['id'])
        if extension == ".epub":
            from bs4 import BeautifulSoup
            segments = []
            with zipfile.ZipFile(path) as archive:
                archive_members(archive)
                items = [i for i in archive.infolist() if Path(i.filename).suffix.lower() in {".xhtml", ".html", ".htm"}]
                if sum(i.file_size for i in items) > 50_000_000:
                    raise ValueError("EPUB expanded content exceeds 50 MB.")
                for i, item in enumerate(items, 1):
                    soup = BeautifulSoup(archive.read(item), "html.parser")
                    for node in soup(["script", "style"]):
                        node.decompose()
                    title = soup.find(["h1", "h2", "title"])
                    locator = f"Section {i}: {_text(title.get_text()) if title else item.filename}"
                    segments.append({"text": soup.get_text("\n", strip=True), "locator": locator, "modality": "text"})
            return segments, []
        if extension in {".srt", ".vtt"}:
            return self._subtitle_segments(read_text(path)), ["Transcript only; on-screen demonstrations have not been analyzed."]
        if extension in MEDIA_EXTENSIONS:
            return self._extract_media(path, artifacts, source)
        if extension == "legacy-office":
            raise ValueError("Legacy/encrypted Office binary detected. Export an unlocked DOCX, PPTX, XLSX, PDF, or text copy.")
        if extension == "executable":
            from .binary_knowledge import extract
            companions=[{'source_id':row['id'],'filename':row.get('filename')} for row in self.store.list('source')
                if row['id']!=source['id'] and row.get('project_id') in (None,source.get('project_id'))
                and Path(row.get('filename','')).stem==Path(source.get('filename') or path.name).stem
                and Path(row.get('filename','')).suffix.lower() in {'.cpp','.c','.cc','.h','.hpp','.rs','.cs'}]
            return extract(path,artifacts,lambda:self._check_cancelled(source['id']),companions)
        raise ValueError("No parser is available for this binary/format. Export to PDF, a modern Office format, text, or a regular ZIP archive.")

    def _extract_website(self, source, artifacts):
        snapshot = artifacts / "downloaded-resource"
        receipt = fetch_resource(source["url"],snapshot,lambda: self._stop.is_set() or self.store.setting("emergency_stop",False) or (self.store.get("source",source["id"]) or {}).get("status") == "cancelled" or self.store.get("source",source["id"]) is None)
        source.update(downloaded_url=receipt["url"],fetched_at=now(),download_bytes=receipt["bytes"],media_type=receipt["content_type"],content_hash=_digest(snapshot.read_bytes()))
        self._update(source["id"],**{k:source[k] for k in ("downloaded_url","fetched_at","download_bytes","media_type","content_hash")})
        downloaded = {**source,"web_resource":False,"path":str(snapshot),"filename":Path(urlparse(receipt["url"]).path).name or ("index.html" if "html" in receipt["content_type"] else "download")}
        segments,warnings = self._extract(downloaded,artifacts)
        source["title"] = downloaded["title"]
        for segment in segments:
            segment["url"] = receipt["url"]
            segment["retrieved_at"] = source["fetched_at"]
        return segments,warnings

    def _extract_archive(self, source, artifacts, budget=None, depth=0):
        if depth >= MAX_ARCHIVE_DEPTH: raise ValueError("Nested archives exceed three levels; unpack and import the inner archive separately.")
        budget = budget if budget is not None else {"bytes":0,"entries":0,"chars":0}
        segments,warnings,report,frames = [],[],[],[]
        with zipfile.ZipFile(source["path"]) as archive:
            members = archive_members(archive)
            total = sum(i.file_size for i in members)
            if budget["bytes"]+total > MAX_ARCHIVE_BYTES or budget["entries"]+len(members)>MAX_ARCHIVE_ENTRIES:
                raise ValueError("Nested archive totals exceed 256 MiB or 1,000 entries.")
            budget["bytes"] += total
            budget["entries"] += len(members)
            for item in members:
                self._check_cancelled(source["id"])
                if self._stop.is_set() or not self.store.get("source",source["id"]): raise ValueError("Archive import cancelled.")
                if item.is_dir() or "__MACOSX" in Path(item.filename).parts or Path(item.filename).name.startswith("."):
                    continue
                member_name = (source.get("archive_member","")+"!" if source.get("archive_member") else "")+item.filename
                entry = {"member":member_name,"bytes":item.file_size}
                if item.flag_bits & 1:
                    entry.update(status="unsupported",reason="Password-protected member; import an unlocked copy.")
                    report.append(entry); warnings.append(f"{item.filename}: {entry['reason']}"); continue
                directory = artifacts / ("member-"+_digest(item.filename)[:16])
                directory.mkdir(exist_ok=True,mode=0o700)
                file = directory / "resource"
                try:
                    written = 0
                    with archive.open(item) as input_, file.open("wb") as output:
                        while block := input_.read(65536):
                            written += len(block)
                            if written>item.file_size: raise ValueError("Archive member expanded beyond its declared size.")
                            output.write(block)
                    file.chmod(0o600)
                    kind = detect_format(file,item.filename)
                    entry["format"] = kind.lstrip(".")
                    if kind in MEDIA_EXTENSIONS:
                        for suffix in (".srt", ".vtt"):
                            sibling = str(Path(item.filename).with_suffix(suffix))
                            if sibling in archive.namelist():
                                info = archive.getinfo(sibling)
                                if not info.flag_bits & 1 and info.file_size <= 20 * 1024 * 1024:
                                    file.with_suffix(suffix).write_bytes(archive.read(info))
                                    break
                    self._update(source["id"],frames=[])
                    subsource = {**source,"path":str(file),"filename":item.filename,"archive_member":member_name,"web_resource":False}
                    subsegments,notes = self._extract(subsource,directory,budget,depth+1)
                    if kind == ".zip": entry["children"] = (self.store.get("source",source["id"]) or {}).get("import_items",[])
                    # Nested archives already charge their text to the shared budget.
                    if kind != ".zip": budget["chars"] += sum(len(s.get("text","")) for s in subsegments)
                    if budget["chars"] > MAX_EXTRACTED_CHARS: raise ValueError("Archive text exceeds 10 million characters; split this archive.")
                    for segment in subsegments:
                        segment["locator"] = item.filename+" › "+segment.get("locator","Document")
                        segment.setdefault("archive_member",member_name)
                    segments.extend(subsegments)
                    for frame in (self.store.get("source",source["id"]) or {}).get("frames",[]):
                        frame["artifact_id"] = str(Path(frame["path"]).relative_to(self.store.root/"artifacts"/source["id"]))
                        frame["locator"] = item.filename+" › "+frame["locator"]
                        frames.append(frame)
                    entry.update(status="partial" if notes else "parsed",segments=len(subsegments))
                    warnings.extend(f"{item.filename}: {note}" for note in notes)
                except Exception as exc:
                    entry.update(status="error",reason=str(exc)[:400])
                    warnings.append(f"{item.filename}: {entry['reason']}")
                report.append(entry)
                if budget["chars"] > MAX_EXTRACTED_CHARS: break
        self._update(source["id"],import_items=report,frames=frames,frame_count=len(frames))
        if not report: warnings.append("The ZIP contains no regular resource files.")
        return segments,warnings

    def _extract_image(self, source, artifacts):
        from PIL import Image, ImageOps
        with Image.open(source["path"]) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image exceeds 40 megapixels; resize it before importing.")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1920, 1920))
            frame = artifacts / "frame-0000.jpg"
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "white")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            flattened.save(frame, quality=90)
            warnings, segments = [], []
            try:
                import pytesseract
                text = pytesseract.image_to_string(flattened, timeout=30).strip()
                if text:
                    segments.append({"text":text,"locator":"Image OCR","modality":"ocr","frame_path":str(frame)})
            except (ImportError, OSError, RuntimeError):
                pass
        runtime = getattr(self, "media_runtime", None)
        config = self._source_config(self.store.get("source", source["id"]) or source)
        if runtime and config.get("enabled", True) and config.get("vision_enabled", True):
            try:
                description = self._source_vision(runtime, frame, 0, config=config)
                if description:
                    from .local_media import selected_vision_model
                    segments.append({"text":description,"locator":"Visual analysis","modality":"vision",
                                     "frame_path":str(frame),"provider":config.get('vision_provider', 'local'),
                                     "model":selected_vision_model(config)})
            except (ValueError, OSError, httpx.HTTPError) as exc:
                if isinstance(exc, InterruptedError) or config.get('vision_provider') in {'codex_subscription','claude_subscription'}:
                    raise
                warnings.append(f"Visual analysis unavailable ({type(exc).__name__}); check Video AI and reprocess.")
        else:
            warnings.append("Visual model analysis disabled or unavailable; use Video AI setup to enable it.")
        self._update(source["id"],frames=[{"artifact_id":frame.name,"path":str(frame),"time":0,"locator":"Image"}],frame_count=1)
        return segments,warnings

    def _extract_pdf(self, path: Path,source_id=None) -> tuple[list[dict], list[str]]:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("This PDF is encrypted; import an unlocked copy.")
        segments, warnings = [], []
        supplements={}
        if source_id:
            record=self.store.get('source',source_id) or {}
            checkpoint=self.store.root/'artifacts'/source_id/'pdf-page-analysis.json'
            if checkpoint.is_file():
                saved=json.loads(checkpoint.read_text())
                if saved.get('source_hash')==record.get('content_hash'):
                    supplements={entry['page']:entry for entry in saved.get('pages',[]) if isinstance(entry.get('page'),int) and isinstance(entry.get('text'),str)}
        for i, page in enumerate(reader.pages):
            if source_id:self._check_cancelled(source_id)
            text = _text(page.extract_text())
            image_page=len(text)<20
            modality = "text"
            if len(text) < 20:
                try:
                    import pymupdf
                    import pytesseract
                    from PIL import Image
                    with pymupdf.open(path) as pdf:
                        bitmap = pdf[i].get_pixmap(matrix=pymupdf.Matrix(2, 2))
                        ocr_text = _text(pytesseract.image_to_string(Image.frombytes("RGB", [bitmap.width, bitmap.height], bitmap.samples)))
                    if ocr_text:
                        text, modality = ocr_text, "ocr"
                except (ImportError, OSError, RuntimeError) as exc:
                    warnings.append(f"Page {i + 1} needs OCR: {type(exc).__name__}. Install the OCR extras and Tesseract.")
            if source_id and image_page:
                from .pdf_vision import analyze_page
                try:
                    interpreted=analyze_page(self,path,i+1,self.store.get('source',source_id) or record,supplements.get(i+1))
                    if interpreted:supplements[i+1]=interpreted
                except InterruptedError:raise
                except Exception as exc:
                    warnings.append(f'Page {i+1}: analysis with the selected model could not finish: {str(exc)[:350]}. Completed text and previous cited descriptions are preserved; Continue retries this page.')
            if _text(text):
                segments.append({"text": text, "locator": f"Page {i + 1}", "modality": modality, "page": i + 1})
            elif i+1 not in supplements:
                warnings.append(f"Page {i + 1} contains no extracted text.")
            if i+1 in supplements:
                item=supplements[i+1]
                segments.append({**{key:item[key] for key in ('text','frame_path','model','provider','assertion_status','origin') if key in item},'locator':f'Page {i+1} · image interpretation','page':i+1,'modality':'vision'})
                from .pdf_vision import VERSION
                prefix='PDF image reading note: ' if item.get('analysis_version')==VERSION else ''
                warnings.extend(prefix+str(value)[:1000] for value in item.get('warnings',[])[:10])
        return segments, warnings

    @staticmethod
    def _subtitle_segments(text: str) -> list[dict]:
        segments = []
        pattern = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.DOTALL)
        for match in pattern.finditer(text):
            g = match.groups()
            start = int(g[0] or 0) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
            end = int(g[4] or 0) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
            content = re.sub(r"<[^>]+>", "", g[8]).strip()
            if content:
                segments.append({"text": content, "locator": f"{_seconds_label(start)}–{_seconds_label(end)}",
                                 "modality": "transcript", "start": start, "end": end})
        return segments

    def _run(self, arguments: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
        generation = getattr(self._processing, 'generation', self._cancel_generation)
        def check():
            if self._stop.is_set() or generation != self._cancel_generation or self.store.setting('emergency_stop', False):
                raise InterruptedError('Source processing stopped by operator.')
            source_id = getattr(self._processing, 'source_id', None)
            source = self.store.get('source', source_id) if source_id else None
            if source and source.get('media_control') == 'cancel':
                raise InterruptedError('Video processing cancelled by user.')
        check()
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        try:
            while True:
                check()
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    out, err = process.communicate(timeout=min(0.2, remaining))
                    check()
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise
        result = subprocess.CompletedProcess(arguments, process.returncode, out, err)
        if result.returncode:
            raise ValueError(f"{Path(arguments[0]).name} failed: {result.stderr[-600:]}")
        return result

    def _extract_media(self, path: Path, artifacts: Path, source: dict, transcript: list[dict] | None = None, *, audio_only=False) -> tuple[list[dict], list[str]]:
        """Checkpointed frame + Whisper analysis, preserving timestamp provenance.

        Speech segments and screenshots are separate evidence sources; the index
        connects them chronologically but never claims that a sampled frame
        proves a click occurred between frames.
        """
        from .local_media import binary, selected_vision_model
        runtime = getattr(self, "media_runtime", None)
        from .source_models import media_config
        base_settings = runtime.config() if runtime else {"enabled": False, "frame_interval": 30, "max_frames": 48}
        settings = media_config(base_settings, self.store.get("source", source["id"]) or source)
        visual_provider = settings.get('vision_provider', 'local')
        visual_model = selected_vision_model(settings)
        from .media_routing import HOSTED, frame_config, detail_config, preserves_hosted_evidence, cached_context_key, VisualProviderFailure
        source = self.store.get('source', source['id']) or source
        preserve_hosted=preserves_hosted_evidence(settings,source)
        if preserve_hosted:
            self._update(source['id'],media_mixed_providers=True)
        low_memory = visual_provider == 'local' and settings.get('local_vision_low_memory',False)
        vision_cache_model = f'{visual_provider}:{visual_model}' + (':low-memory-v1' if low_memory else '') + (':effort-'+(settings.get('vision_effort') or 'provider') if settings.get('vision_effort','medium')!='medium' else '')
        if not transcript:
            for suffix in (".srt", ".vtt"):
                sidecar = path.with_suffix(suffix)
                if sidecar.is_file():
                    transcript = self._subtitle_segments(read_text(sidecar))
                    break
        ffmpeg, ffprobe = binary("ffmpeg"), binary("ffprobe")
        if not ffmpeg or not ffprobe:
            return transcript or [], ["FFmpeg and ffprobe are required; install them in Video AI setup."]
        probe = json.loads(self._run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 60).stdout)
        duration = float(probe.get("format", {}).get("duration") or 0)
        has_audio = any(item.get("codec_type") == "audio" for item in probe.get("streams", []))
        has_video = any(item.get("codec_type") == "video" for item in probe.get("streams", []))
        video_stream = next((item for item in probe.get('streams', []) if item.get('codec_type') == 'video'), {})
        try:
            video_duration = float(video_stream.get('duration'))
            if not math.isfinite(video_duration) or video_duration <= 0: video_duration = duration
        except (TypeError, ValueError):
            video_duration = duration
        segments, warnings = list(transcript or []), []
        artifacts.mkdir(parents=True, exist_ok=True)
        sid = source["id"]
        timeline_path = artifacts / "media_timeline.json"
        timeline = json.loads(timeline_path.read_text()) if timeline_path.exists() else []
        from .media_context import CONTEXT_VERSION, context_key, narration_near, retained_timeline, save_json
        timeline = [e for e in timeline if e.get('modality') != 'vision' or (
            e.get('context_version') == CONTEXT_VERSION and
            (preserve_hosted or
             (e.get('provider') == visual_provider and e.get('model') == visual_model)))]
        if not audio_only and runtime and settings.get('enabled',True) and settings.get('vision_enabled',True):
            with self.store.connection(immediate=True) as db:
                for old in db.execute("SELECT * FROM knowledge_chunks WHERE source_id=? AND modality='vision' AND corrected=0",(sid,)).fetchall():
                    meta = json.loads(old['metadata'])
                    mismatch=(False if preserve_hosted else
                        meta.get('provider') != visual_provider or meta.get('model') != visual_model)
                    if meta.get('active',True) and (mismatch or meta.get('context_version') != CONTEXT_VERSION):
                        self._deactivate_chunk(db,old,'superseded','Visual provider, model or evidence version changed.')

        if has_audio and not transcript:
            model_path = (runtime.whisper_path() if runtime else
                          Path(self._settings().get("whisper_model_path") or os.environ.get("BUGHUNTER_WHISPER_MODEL") or ""))
            whisper = binary("whisper-cli")
            if runtime and not settings.get("speech_enabled", True):
                warnings.append("Local speech transcription disabled in Video AI setup.")
            elif whisper and model_path.is_file():
                from .media_speech import transcribe_chunks
                audio_stream = next(item for item in probe['streams'] if item.get('codec_type') == 'audio')
                try:
                    audio_duration = float(audio_stream.get('duration'))
                    if not math.isfinite(audio_duration) or audio_duration <= 0: audio_duration = duration
                except (TypeError, ValueError):
                    audio_duration = duration
                segments.extend(transcribe_chunks(self, runtime, source, path, artifacts,
                                audio_duration, ffmpeg, whisper, model_path,
                                publish=lambda completed: self._publish_media(source, completed, artifacts, cached=timeline)))
            else:
                warnings.append("Local Whisper is not ready. In Video AI, install whisper.cpp and the speech model, then reprocess.")
        # Speech is already useful while the much longer visual pass runs.
        if segments:
            self._publish_media(source, segments, artifacts, cached=timeline)
        if audio_only:
            if not has_audio: warnings.append('No audio stream was detected.')
            return segments, warnings
        if has_video:
            if has_audio and video_duration + 1 < duration:
                warnings.append(MEDIA_AUDIO_TAIL_NOTE)
            interval = float(settings.get("frame_interval", 30))
            cap = int(settings.get("max_frames", 0))
            from .media_sampling import scene_cuts, plan_frames
            # Sample every configured interval plus moments where the visible UI
            # changes sharply. A scene cut is NOT proof a button was clicked.
            extra_times = []
            if settings.get("adaptive_frames", True) and settings.get("vision_enabled",True) and video_duration:
                self._update(sid, media_progress={"phase":"scene_scan", "analyzed":0,
                             "total":0, "timestamp":0, "duration":video_duration})
                from .media_sampling import scan_windows
                stat = path.stat()
                scene_identity = {'source_hash':source.get('content_hash'), 'size':stat.st_size,
                                  'mtime_ns':stat.st_mtime_ns, 'duration':video_duration}
                extra_times, scene_gaps = scan_windows(ffmpeg, str(path), video_duration,
                    artifacts / 'scene-cuts-v2.json', scene_identity,
                    threshold=float(settings.get('scene_threshold', .30)), run=self._run,
                    check=lambda: self._check_cancelled(sid),
                    progress=lambda value: self._update(sid, media_progress=value))
                self._update(sid, scene_coverage={'total':math.ceil(video_duration/600),
                    'completed':math.ceil(video_duration/600)-len(scene_gaps), 'gaps':scene_gaps})
                if scene_gaps:
                    warnings.append(f"Scene scan has {len(scene_gaps)} incomplete ten-minute windows; fixed-interval frames remain available.")
            stamps = plan_frames(video_duration, interval, cap, extra_times)
            self._update(sid, sampling_plan={'planned':stamps, 'duration':video_duration,
                'interval':interval, 'max_frames':cap,
                'adaptive_frames':bool(settings.get('adaptive_frames', True)),
                'scene_threshold':settings.get('scene_threshold', 0.30)})
            total_frames = len(stamps)
            self._processing.saved_visual_times={round(float(t['start']),2) for t in timeline if t.get('modality') in {'vision','ocr'} and t.get('start') is not None}&{round(stamp,2) for stamp in stamps}
            self._update(sid, media_progress={"phase":"frames", "analyzed":0,
                         "total":total_frames, "timestamp":0, "duration":video_duration})
            step = interval
            frames = []
            existing = {round(float(t.get("start", -1)), 2): t for t in timeline if t.get("modality") == "vision"}
            visual_count = 0
            ocr_count = 0
            vision_usable = bool(runtime and settings.get("enabled", True) and settings.get("vision_enabled", True))
            had_model_error = False
            def check_visual_cancelled():
                self._check_cancelled(sid)
                if self._stop.is_set():
                    raise InterruptedError('Application shutting down.')
                latest = self.store.get('source', sid)
                if latest is None or latest.get('media_control') == 'cancel':
                    raise InterruptedError('Source visual analysis cancelled.')
            for index, stamp in enumerate(stamps):
                check_visual_cancelled()
                if runtime: runtime.checkpoint(sid, self)
                latest = self.store.get('source', sid) or source
                selected_settings=frame_config(media_config(base_settings,latest),getattr(self._processing,'lane',0))
                preserve_hosted=preserves_hosted_evidence(selected_settings,latest)
                vision_usable = bool(runtime and selected_settings.get('enabled', True) and selected_settings.get('vision_enabled', True))
                low_memory = selected_settings.get('vision_provider','local') == 'local' and selected_settings.get('local_vision_low_memory',False)
                visual_provider=selected_settings.get('vision_provider','local')
                visual_model=selected_vision_model(selected_settings)
                vision_cache_model=f'{visual_provider}:{visual_model}'+(':low-memory-v1' if low_memory else '') + (':effort-'+(selected_settings.get('vision_effort') or 'provider') if selected_settings.get('vision_effort','medium')!='medium' else '')
                # Timestamp-keyed paths avoid silently reusing the wrong JPEG
                # when the interval or frame cap changes between reprocesses.
                frame = artifacts / f"frame-{int(round(stamp * 1000)):012d}.jpg"
                if not frame.exists():
                    try:
                        self._run([ffmpeg, "-nostdin", "-y", "-ss", str(stamp), "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                                   "-vf", f"scale='min({768 if low_memory else 1000},iw)':-2", "-q:v", "5", str(frame)], 90)
                    except InterruptedError:
                        raise
                    except (ValueError, OSError, subprocess.SubprocessError):
                        # A single unseekable/corrupt frame must not discard the whole transcript.
                        warnings.append(f"Skipped an unreadable frame at {_seconds_label(stamp)}.")
                        continue
                if not frame.exists():
                    continue
                detailed = artifacts / f"frame-{int(round(stamp * 1000)):012d}-detail.jpg"
                if detailed.exists():
                    frame=detailed
                    selected_settings=detail_config(selected_settings)
                    visual_model=selected_vision_model(selected_settings)
                    vision_cache_model=f'{visual_provider}:{visual_model}'+(':low-memory-v1' if low_memory else '') + (':effort-'+(selected_settings.get('vision_effort') or 'provider') if selected_settings.get('vision_effort','medium')!='medium' else '')
                current_frame = {"artifact_id":frame.name,"path":str(frame),"time":stamp,
                                 "locator":f"Frame at {_seconds_label(stamp)}"}
                previous_frames = [] if low_memory else frames[-2:]
                nearby = narration_near(segments, stamp)
                cache_key = context_key(source.get('content_hash'), vision_cache_model,
                                        current_frame, previous_frames, nearby)
                frames.append(current_frame)
                visual = existing.get(round(stamp, 2))
                if visual:
                    previous_key = (cached_context_key(visual, source.get('content_hash'), current_frame,
                        nearby, artifacts) if preserve_hosted else context_key(source.get('content_hash'),
                        vision_cache_model, current_frame, previous_frames, nearby))
                    with self.store.connection() as db:
                        rejected = db.execute("SELECT 1 FROM knowledge_chunks WHERE source_id=? AND json_extract(metadata,'$.context_key')=? AND json_extract(metadata,'$.review_status')='rejected' LIMIT 1",
                            (sid, visual.get('context_key'))).fetchone()
                    if not previous_key or visual.get('context_key') != previous_key or rejected:
                        visual=None
                if visual is None and vision_usable:
                    try:
                        description = self._source_vision(runtime, frame, stamp, context=nearby,
                            previous_frames=previous_frames, cancel_check=check_visual_cancelled, config=selected_settings,
                            detail=(frame == detailed))
                        if description and re.search(r'(?:unreadable|illegible|too small to read|cannot read|insufficiently legible|text is small|неразборчив|невозможно прочитать)', description, re.I) and frame != detailed:
                            check_visual_cancelled()
                            self._run([ffmpeg, '-nostdin', '-y', '-ss', str(stamp), '-i', str(path),
                                '-map', '0:v:0', '-frames:v', '1', '-vf', f"scale='min({1600 if low_memory else 2000},iw)':-2", '-q:v', '3', str(detailed)], 90)
                            if detailed.exists():
                                frame = detailed
                                current_frame.update(path=str(frame), artifact_id=frame.name)
                                selected_settings=detail_config(selected_settings)
                                visual_model=selected_vision_model(selected_settings)
                                vision_cache_model=f'{visual_provider}:{visual_model}'+(':low-memory-v1' if low_memory else '') + (':effort-'+(selected_settings.get('vision_effort') or 'provider') if selected_settings.get('vision_effort','medium')!='medium' else '')
                                cache_key = context_key(source.get('content_hash'), vision_cache_model, current_frame, previous_frames, nearby)
                                description = self._source_vision(runtime, frame, stamp, context=nearby, previous_frames=previous_frames, cancel_check=check_visual_cancelled, config=selected_settings, detail=True)
                        if not description:
                            raise ValueError('Vision provider returned no evidence.')
                        if description:
                            visual = {"text":description,"locator":f"Frame at {_seconds_label(stamp)}",
                                      "modality":"vision","start":stamp,"end":min(video_duration,stamp+step),
                                      "frame_path":str(frame),"model":visual_model,"provider":visual_provider,
                                      "assertion_status":"inferred", "origin":"model",
                                      "context_version":CONTEXT_VERSION, "context_key":cache_key,"effort":selected_settings.get("vision_effort","medium"),
                                      "vision_profile":"low_memory" if low_memory else "standard",
                                      "narration_context":nearby,
                                      "previous_frames":[{'artifact_id':f['artifact_id'],'time':f['time']} for f in previous_frames]}
                            existing[round(stamp, 2)] = visual
                    except Exception as exc:  # Preserve every completed frame before provider failure.
                        if isinstance(exc, InterruptedError):
                            raise
                        self._publish_media(source, segments, artifacts, cached=list(existing.values()))
                        self._register_media_frames(sid,frames)
                        self._update(sid, visual_context_count=visual_count, ocr_frame_count=ocr_count,
                            media_progress={'phase':'frames','analyzed':index,'total':total_frames,
                                'timestamp':stamps[index-1] if index else 0,
                                'visual_context':visual_count,'ocr':ocr_count},
                            visual_failure={'timestamp':stamp,'error':redact_text(str(exc))[:500]})
                        raise VisualProviderFailure(f'Visual analysis failed at {_seconds_label(stamp)}: {redact_text(str(exc))[:500]}. Completed evidence is saved; retry to resume.',visual_provider,visual_model) from exc
                if visual:
                    segments.append(visual)
                    self._processing.saved_visual_times.add(round(stamp,2))
                    visual_count += 1
                else:
                    try:
                        import pytesseract
                        from PIL import Image
                        text = _text(pytesseract.image_to_string(Image.open(frame), timeout=30))
                        if text:
                            segments.append({"text":text,"locator":f"Frame at {_seconds_label(stamp)}",
                                             "modality":"ocr","start":stamp,"frame_path":str(frame)})
                            ocr_count += 1
                    except (ImportError, OSError, RuntimeError):
                        pass
                # The timeline checkpoints every completed frame; searchable
                # batches preserve prior corrections and source-linked notes.
                cached_visuals = list(existing.values())
                save_json(timeline_path, retained_timeline(segments, cached_visuals))
                if index == 0 or (index + 1) % 10 == 0:
                    self._publish_media(source, segments, artifacts, cached=cached_visuals)
                    save_json(artifacts / 'frames.json', frames)
                    self._register_media_frames(sid,frames)
                    self._update(sid, visual_context_count=visual_count, ocr_frame_count=ocr_count,
                        media_progress={"phase":"frames", "analyzed":index+1,"total":total_frames,
                            "timestamp":stamp, "visual_context":visual_count,"ocr":ocr_count}, duration=duration)
            (artifacts / "frames.json").write_text(json.dumps(frames))
            self._register_media_frames(sid,frames)
            self._update(sid, duration=duration, video_duration=video_duration,
                         visual_context_count=visual_count, ocr_frame_count=ocr_count,
                         media_progress={"phase":"frames", "analyzed":len(frames),"total":total_frames,
                            "timestamp":video_duration,"visual_context":visual_count,"ocr":ocr_count})
            if not vision_usable and not existing:
                warnings.append("Only sampled frames and available OCR were indexed. Enable a vision provider in Video AI for frame descriptions.")
            missing = total_frames - visual_count - ocr_count
            self._update(sid, media_coverage={"planned":total_frames,"sampled":len(frames),
                "vision":visual_count,"ocr":ocr_count,"missing":missing})
            if missing:
                warnings.append(f"{missing} planned frames have no searchable visual or OCR evidence.")
            warnings.append(MEDIA_SAMPLING_NOTE)
        elif not has_audio:
            warnings.append("No audio or video stream was detected.")
        if segments:
            self._publish_media(source, segments, artifacts)
        return segments, warnings

    def _fetch_youtube(self, video_id: str, language: str) -> list[dict]:
        from youtube_transcript_api import YouTubeTranscriptApi
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=[language])
        return [{"text": row.text, "start": float(row.start), "duration": float(row.duration)} for row in fetched]

    def _extract_youtube(self, source: dict, artifacts: Path) -> tuple[list[dict], list[str]]:
        segments, warnings = [], []
        caption_error = None
        try:
            rows = self._fetch_youtube(source["video_id"], source.get("language", "en"))
            for row in rows:
                start = row["start"]
                end = start + row["duration"]
                segments.append({"text": row["text"], "locator": f"{_seconds_label(start)}–{_seconds_label(end)}",
                    "modality": "transcript", "start": start, "end": end,
                    "url": f"{source['url']}&t={int(start)}s"})
        except Exception as exc:  # noqa: BLE001 - upstream library uses several non-HTTP exception types
            caption_error = f"YouTube captions unavailable ({type(exc).__name__})."
        if source.get("download_media"):
            downloaded = artifacts / "youtube.mp4"
            self._download_youtube(source["url"], downloaded)
            if not downloaded.exists():
                raise ValueError("The selected YouTube media could not be downloaded. Captions may still be available without downloading.")
            media, media_warnings = self._extract_media(downloaded, artifacts, source, segments)
            return media, media_warnings
        if caption_error:
            warnings.append(caption_error + " Retry later, import a transcript, or explicitly enable media download.")
        warnings.append("YouTube captions only; video frames have not been analyzed. Media download is opt-in.")
        return segments, warnings

    @staticmethod
    def _download_youtube(url: str, destination: Path):
        # The Python API works in the packaged sidecar and never reads CLI config
        # files, cookies or arbitrary user postprocessor/exec hooks.
        import yt_dlp

        class QuietLogger:
            def debug(self, message):
                pass

            def warning(self, message):
                pass

            def error(self, message):
                pass

        options = {"noplaylist": True, "quiet": True, "noprogress": True,
                   "max_filesize": MAX_FILE_BYTES, "socket_timeout": 30, "retries": 1,
                   "format": "best[height<=480][ext=mp4]/best[height<=480]",
                   "outtmpl": str(destination), "logger": QuietLogger(), "cachedir": False}
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])

    def _index(self, source: dict, segments: list[dict], *, preserve_existing=False) -> int:
        id = source["id"]
        records = []
        seen = set()
        for segment in segments:
            for index, (text, section) in enumerate(_sectioned_chunks(segment.get("text", ""))):
                locator = segment.get("locator", "Document")
                identity = str(segment.get("context_key") or "") if segment.get("modality") == "vision" else ""
                if segment.get('modality')=='vision' and segment.get('recovery_id'):
                    identity+=':'+str(segment['recovery_id'])
                chunk_id = f"chunk_{_digest(id + locator + str(index) + text + identity)[:24]}"
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                metadata = {k: v for k, v in segment.items() if k not in {"text", "locator", "modality"}}
                if section:
                    metadata["section"] = section
                records.append({"id": chunk_id, "text": text, "locator": locator,
                                "modality": segment.get("modality", "text"), "metadata": metadata})
        if preserve_existing:
            return self._append_media_records(source, records)
        with self.store.connection(immediate=True) as db:
            if not db.execute("SELECT 1 FROM entities WHERE kind='source' AND id=?", (id,)).fetchone():
                return 0
            existing = {r["id"]: dict(r) for r in db.execute("SELECT * FROM knowledge_chunks WHERE source_id=?", (id,))}
            valid_ids = {r["id"] for r in records}
            for old in existing.values():
                if old['id'] not in valid_ids and (old['corrected'] or old['modality']=='vision_interpretation'):
                    records.append({'id':old['id'],'text':old['original_text'],'locator':old['locator'],
                                    'modality':old['modality'],'metadata':json.loads(old['metadata'])})
                    valid_ids.add(old['id'])
            for old_id in existing.keys() - valid_ids:
                old=existing[old_id]
                if not json.loads(old['metadata']).get('active',True):
                    continue
                if old['modality']=='vision':
                    self._deactivate_chunk(db,old,'superseded','Replaced during source visual reanalysis.')
                    continue
                self._invalidate_examples(db,old_id)
                db.execute("DELETE FROM knowledge_chunks WHERE id=?", (old_id,))
                db.execute("DELETE FROM knowledge_vectors WHERE chunk_id=?", (old_id,))
                db.execute("DELETE FROM knowledge_nodes WHERE id=?", (old_id,))
                db.execute("DELETE FROM knowledge_edges WHERE chunk_id=? OR source=? OR target=?", (old_id, old_id, old_id))
            db.execute("DELETE FROM knowledge_fts WHERE source_id=?", (id,))
            db.execute("DELETE FROM knowledge_edges WHERE source_id=? AND json_extract(data,'$.origin') IN ('lexical','timestamp')", (id,))
            self._node(db, id, "source", source["title"], {"source_id": id, "status": "imported"})
            for ordinal, record in enumerate(records):
                old = existing.get(record["id"])
                text = old["text"] if old and old["corrected"] else record["text"]
                if old and old['text'] != text: self._invalidate_examples(db,record['id'])
                corrected = old["corrected"] if old else 0
                if old and not json.loads(old['metadata']).get('active',True):
                    # Regenerating identical text cannot reverse an explicit
                    # withdrawal. Preserve its review metadata and audit row.
                    record['metadata'].update(json.loads(old['metadata']))
                db.execute("INSERT OR REPLACE INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,?)", (
                    record["id"], id, ordinal, text, record["text"], record["locator"], record["modality"], json.dumps(record["metadata"]), corrected))
                if not record['metadata'].get('active',True):
                    continue
                db.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)", (record["id"], id, text, _fts_title(source["title"], record["metadata"].get("section", "")), record["locator"]))
                self._node(db, record["id"], "example", text[:90], {"source_id": id, "chunk_id": record["id"], "locator": record["locator"],
                    "status": "corrected" if corrected else "imported", "modality": record["modality"]})
                self._edge(db, id, record["id"], "contains", id, record["id"], {"origin": "lexical", "status": "imported"})
                self._lexical_graph(db, id, record["id"], text, record["locator"])
            # Evidence-backed temporal edges between indexed transcript/vision chunks.
            timed = [(float(r["metadata"]["start"]), r["id"]) for r in records if r['metadata'].get('active',True) and isinstance(r["metadata"].get("start"), (int, float))]
            timed.sort(key=lambda pair: (pair[0], pair[1]))
            for (at, earlier), (next_at, later) in zip(timed, timed[1:]):
                if earlier != later:
                    self._edge(db, earlier, later, "precedes", id, earlier,
                               {"origin":"timestamp", "start":at, "next_start":next_at, "status":"observed_order"})
            self._remove_orphan_nodes(db)
        return sum(r['metadata'].get('active',True) for r in records)

    def _deactivate_chunk(self, db, row, status, reason):
        metadata = json.loads(row['metadata'])
        metadata.update(active=False, review_status=status, review_reason=reason, reviewed_at=now())
        db.execute('UPDATE knowledge_chunks SET metadata=? WHERE id=?', (json.dumps(metadata),row['id']))
        db.execute('DELETE FROM knowledge_fts WHERE id=?', (row['id'],))
        db.execute('DELETE FROM knowledge_vectors WHERE chunk_id=?', (row['id'],))
        db.execute("UPDATE knowledge_nodes SET data=json_set(data,'$.active',json('false'),'$.status',?) WHERE id=? OR (json_extract(data,'$.chunk_id')=? AND json_extract(data,'$.origin')='model')", (status,row['id'],row['id']))
        db.execute('DELETE FROM knowledge_edges WHERE chunk_id=? OR source=? OR target=?', (row['id'],row['id'],row['id']))
        self._invalidate_examples(db, row['id'])
        # Procedure titles and steps can repeat a rejected observation even
        # after its direct graph edges disappear. Withdraw the whole derived
        # candidate while retaining its entity as an audit record.
        procedures=db.execute("SELECT id,data FROM entities WHERE kind='course_procedure' AND json_extract(data,'$.source_id')=?",(row['source_id'],)).fetchall()
        for procedure in procedures:
            value=json.loads(procedure['data'])
            if not any(step.get('chunk_id')==row['id'] for step in value.get('steps',[])):
                continue
            value.update(active=False,status='withdrawn',withdrawal_reason=reason,withdrawn_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?",(json.dumps(value),now(),procedure['id']))
            for node_id in [procedure['id'],*[step['id'] for step in value.get('steps',[])]]:
                db.execute("UPDATE knowledge_nodes SET data=json_set(data,'$.active',json('false'),'$.status','withdrawn') WHERE id=?",(node_id,))
                db.execute('DELETE FROM knowledge_edges WHERE source=? OR target=?',(node_id,node_id))
        self._remove_orphan_nodes(db)

    def reject(self, id, reason='Rejected by operator'):
        reason = _text(reason)
        if not reason or len(reason)>3000:
            raise ValueError('Provide a rejection reason of 1–3,000 characters.')
        with self.store.connection(immediate=True) as db:
            row = db.execute('SELECT * FROM knowledge_chunks WHERE id=?',(id,)).fetchone()
            if not row: raise ValueError('Source chunk not found.')
            sid = row['source_id']
            self._deactivate_chunk(db,row,'rejected',reason)
        root = self.store.root / 'artifacts' / sid
        from .media_context import save_json
        timeline = root / 'media_timeline.json'
        if timeline.exists():
            entries = json.loads(timeline.read_text())
            metadata = json.loads(row['metadata'])
            removed = [e for e in entries if e.get('modality')==row['modality'] and e.get('locator')==row['locator'] and e.get('context_key')==metadata.get('context_key')]
            save_json(root / ('rejected-'+id+'.json'), {'chunk_id':id,'reason':reason,'entries':removed,'at':now()})
            save_json(timeline, [e for e in entries if e not in removed])
        # Final extraction caches can contain the rejected generated passage.
        (root / 'extracted.json').unlink(missing_ok=True)
        self.store.event('knowledge.rejected', {'source_id':sid,'chunk_id':id,'reason':reason})
        with self.store.connection() as db:
            count = db.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1",(sid,)).fetchone()[0]
        self._update(sid, semantic_index='stale',chunk_count=count)
        return {'id':id,'source_id':sid,'status':'rejected','reason':reason}

    def _append_media_records(self, source, records):
        """Publish completed evidence without replacing corrections or learned references."""
        sid = source['id']
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (sid,)).fetchone()
            if not row:
                return 0
            current = json.loads(row[0])
            if current.get('status') == 'cancelled':
                raise InterruptedError('Source processing stopped by operator.')
            existing = {r['id'] for r in db.execute('SELECT id FROM knowledge_chunks WHERE source_id=?', (sid,))}
            ordinal = db.execute('SELECT coalesce(max(ordinal),-1)+1 FROM knowledge_chunks WHERE source_id=?', (sid,)).fetchone()[0]
            self._node(db, sid, 'source', source['title'], {'source_id':sid, 'status':'imported'})
            for record in records:
                if record['id'] in existing:
                    continue
                metadata = record['metadata']
                if record['modality'] == 'vision':
                    if db.execute("SELECT 1 FROM knowledge_chunks WHERE source_id=? AND modality='vision' AND locator=? AND corrected=1", (sid,record['locator'])).fetchone():
                        continue
                    old_rows = db.execute("SELECT * FROM knowledge_chunks WHERE source_id=? AND modality='vision' AND locator=? AND corrected=0", (sid,record['locator'])).fetchall()
                    for old in old_rows:
                        if old['id'] != record['id'] and json.loads(old['metadata']).get('active',True) and (json.loads(old['metadata']).get('context_key') != metadata.get('context_key') or (not metadata.get('context_key') and old['id'] not in {r['id'] for r in records})):
                            self._deactivate_chunk(db,old,'superseded','Replaced by current frame evidence generation.')
                status = 'inferred' if record['modality'] in {'vision','vision_interpretation'} else 'imported'
                db.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,0)',
                           (record['id'], sid, ordinal, record['text'], record['text'], record['locator'],
                            record['modality'], json.dumps(metadata)))
                db.execute('INSERT INTO knowledge_fts VALUES(?,?,?,?,?)',
                           (record['id'], sid, record['text'], _fts_title(source['title'], metadata.get('section','')), record['locator']))
                self._node(db, record['id'], 'example', record['text'][:90],
                           {'source_id':sid, 'chunk_id':record['id'], 'locator':record['locator'], 'status':status,
                            'modality':record['modality'], 'origin':'model' if status=='inferred' else 'extraction'})
                self._edge(db, sid, record['id'], 'contains', sid, record['id'], {'origin':'lexical','status':status})
                self._lexical_graph(db, sid, record['id'], record['text'], record['locator'])
                existing.add(record['id']); ordinal += 1
            db.execute("DELETE FROM knowledge_edges WHERE source_id=? AND json_extract(data,'$.origin')='timestamp'", (sid,))
            timed = []
            for row in db.execute("SELECT id,metadata FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1", (sid,)):
                start = json.loads(row['metadata']).get('start')
                if isinstance(start, (int,float)) and math.isfinite(start):
                    timed.append((float(start), row['id']))
            timed.sort()
            for (at, earlier), (next_at, later) in zip(timed, timed[1:]):
                self._edge(db, earlier, later, 'precedes', sid, earlier,
                           {'origin':'timestamp','start':at,'next_start':next_at,'status':'observed_order'})
            count = db.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1", (sid,)).fetchone()[0]
            current.update(chunk_count=count, semantic_index='pending', updated_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE kind='source' AND id=?",
                       (json.dumps(current), current['updated_at'], sid))
        return count

    def _publish_media(self, source, segments, artifacts, cached=()):
        from .media_context import retained_timeline, save_json
        self._check_cancelled(source['id'])
        ordered = sorted(segments, key=lambda s:(float(s.get('start',0)), s.get('modality','')))
        save_json(artifacts / 'media_timeline.json', retained_timeline(ordered, cached))
        count = self._index(source, ordered, preserve_existing=True)
        self._update(source['id'], media_indexed_at=now(), media_indexed_passages=count)
        return count

    def _register_media_frames(self, source_id, frames):
        """Retain previously cited and manually reviewed images on resumption."""
        with self.store.connection(immediate=True) as db:
            row=db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(source_id,)).fetchone()
            if not row:
                return
            source=json.loads(row[0])
            merged={frame['path']:frame for frame in [*source.get('frames',[]),*frames]}
            source.update(frames=sorted(merged.values(),key=lambda frame:(frame.get('time',0),frame['path'])),
                          frame_count=len(merged),updated_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?",(json.dumps(source),source['updated_at'],source_id))

    @staticmethod
    def _invalidate_examples(db,chunk_id):
        db.execute("UPDATE entities SET data=json_set(data,'$.reviewed',json('false'),'$.review_stale',json('true')),updated_at=? WHERE kind='learning_example' AND json_extract(data,'$.chunk_id')=?",(now(),chunk_id))

    @staticmethod
    def _node(db, id: str, kind: str, label: str, data: dict):
        db.execute("INSERT OR REPLACE INTO knowledge_nodes VALUES(?,?,?,?)", (id, kind, label, json.dumps(data)))

    @staticmethod
    def _edge(db, source: str, target: str, relation: str, source_id: str, chunk_id: str | None, data: dict):
        id = f"edge_{_digest('|'.join((source, target, relation, chunk_id or '')))[:24]}"
        db.execute("INSERT OR REPLACE INTO knowledge_edges VALUES(?,?,?,?,?,?,?)", (id, source, target, relation, source_id, chunk_id, json.dumps(data)))

    def _lexical_graph(self, db, source_id: str, chunk_id: str, text: str, locator: str):
        for kind, label, pattern in CONCEPTS:
            if re.search(pattern, text, re.IGNORECASE):
                concept = f"concept_{_digest(kind + label)[:18]}"
                self._node(db, concept, kind, label, {"status": "inferred", "origin": "lexical"})
                self._edge(db, chunk_id, concept, "mentions", source_id, chunk_id,
                           {"origin": "lexical", "status": "inferred", "locator": locator})

    def _embed(self, texts: list[str]) -> tuple[str, list[list[float]]]:
        settings = self._settings()
        model = settings.get("embedding_model", "nomic-embed-text")
        base = settings.get("embedding_base_url", "http://127.0.0.1:11434").rstrip("/")
        parsed = urlparse(base)
        host = parsed.hostname or ""
        # Embeddings never go to a hosted endpoint, even when reasoning is hosted.
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("Embedding endpoint must be a loopback Ollama server.")
        if ":cloud" in model.lower():
            raise ValueError("Cloud embedding models are unavailable in the local knowledge index.")
        with httpx.Client(timeout=httpx.Timeout(60, connect=1), trust_env=False) as client:
            tags = client.get(f"{base}/api/tags")
            tags.raise_for_status()
            models = tags.json().get("models", [])
            matching = next((m for m in models if m.get("name") in {model, model + ":latest"}), None)
            if not matching or matching.get("remote_host") or matching.get("remote_model"):
                raise ValueError(f"Download local embedding model {model!r} in Ollama before enabling semantic search.")
            digest = matching.get("digest", model)
            response = client.post(f"{base}/api/embed", json={"model": model, "input": texts, "keep_alive": 0})
            response.raise_for_status()
        vectors = response.json().get("embeddings", [])
        if len(vectors) != len(texts) or not vectors or not vectors[0] or len(vectors[0]) > 8192:
            raise ValueError("Embedding service returned invalid dimensions or count.")
        dimension = len(vectors[0])
        if any(len(v) != dimension or any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in v) for v in vectors):
            raise ValueError("Embedding service returned malformed vectors.")
        return f"{model}@{digest}", vectors

    def _index_vectors(self, source_id: str) -> str:
        if not self._settings().get("use_local_embeddings", False):
            return "disabled"
        if not self._vec_available:
            return "unavailable"
        with self.store.connection() as db:
            chunks = db.execute("SELECT id,text FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1 ORDER BY ordinal", (source_id,)).fetchall()
        if not chunks:
            return "empty"
        # No simultaneous large models: one serialized worker, one small embedding batch.
        for offset in range(0, len(chunks), 16):
            batch = chunks[offset:offset + 16]
            model, vectors = self._embed([c["text"] for c in batch])
            with self.store.connection(immediate=True) as db:
                for chunk, vector in zip(batch, vectors):
                    current = db.execute("SELECT text FROM knowledge_chunks WHERE id=? AND coalesce(json_extract(metadata,'$.active'),1)=1", (chunk["id"],)).fetchone()
                    if current and current[0] == chunk["text"]:
                        db.execute("INSERT OR REPLACE INTO knowledge_vectors VALUES(?,?,?,?)", (chunk["id"], model, len(vector), struct.pack(f"<{len(vector)}f", *vector)))
        return "ready"

    @staticmethod
    def _chunk_record(row, source: dict | None = None) -> dict:
        value = dict(row)
        metadata = json.loads(value.pop("metadata", "{}"))
        return {**value, **metadata, "title": (source or {}).get("title", "Resource"),
                "citation": {"source_id": value["source_id"], "chunk_id": value["id"], "locator": value["locator"],
                             "url": metadata.get("url") or (source or {}).get("url")}}

    def search(self, query: str, limit: int = 10, project_id="*") -> list[dict]:
        query = _text(query)
        if not query:
            return []
        limit = max(1, min(int(limit), 50))
        candidate_limit = limit * 3
        # Apply scope before ranking/limiting; unrelated projects must not crowd
        # the selected project out of either retrieval channel.
        scope_sql = "" if project_id == "*" else " AND (json_extract(e.data, '$.project_id') IS NULL OR json_extract(e.data, '$.project_id')=?)"
        scope_params = () if project_id == "*" else (project_id,)
        terms = re.findall(r"[\w-]+", query, re.UNICODE)[:30]
        if not terms:
            return []
        expression = _expand_query(terms)
        scores: dict[str, float] = {}
        semantic_error = None
        with self.store.connection() as db:
            lexical = db.execute("""SELECT knowledge_fts.id,bm25(knowledge_fts) AS rank FROM knowledge_fts
                JOIN knowledge_chunks c ON c.id=knowledge_fts.id
                JOIN entities e ON e.kind='source' AND e.id=c.source_id
                WHERE knowledge_fts MATCH ? AND coalesce(json_extract(c.metadata,'$.active'),1)=1""" + scope_sql + " ORDER BY rank LIMIT ?",
                (expression, *scope_params, candidate_limit)).fetchall()
            for i, row in enumerate(lexical):
                scores[row["id"]] = 1 / (60 + i)
            vectors_exist = db.execute("SELECT 1 FROM knowledge_vectors LIMIT 1").fetchone()
        if vectors_exist and self._vec_available and self._settings().get("use_local_embeddings", False):
            try:
                model, vectors = self._embed([query])
                vector = vectors[0]
                with self.store.connection() as db:
                    if self._load_vec(db):
                        rows = db.execute("""SELECT v.chunk_id,vec_distance_cosine(v.embedding,?) AS distance
                            FROM knowledge_vectors v JOIN knowledge_chunks c ON c.id=v.chunk_id
                            JOIN entities e ON e.kind='source' AND e.id=c.source_id
                            WHERE v.model=? AND v.dimension=? AND coalesce(json_extract(c.metadata,'$.active'),1)=1""" + scope_sql + " ORDER BY distance LIMIT ?",
                            (struct.pack(f"<{len(vector)}f", *vector), model, len(vector), *scope_params, candidate_limit)).fetchall()
                        for i, row in enumerate(rows):
                            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0) + 1 / (60 + i)
            except Exception as exc:  # noqa: BLE001 - retrieval remains available if a local model fails
                semantic_error = str(exc)[:200]
        results = []
        with self.store.connection() as db:
            for chunk_id in sorted(scores, key=scores.get, reverse=True):
                row = db.execute("SELECT * FROM knowledge_chunks WHERE id=?", (chunk_id,)).fetchone()
                if not row:
                    continue
                source_row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (row["source_id"],)).fetchone()
                if not source_row:
                    continue
                source = json.loads(source_row[0])
                if project_id != "*" and source.get("project_id") not in (None, project_id):
                    continue
                concepts = db.execute("SELECT n.label,n.kind,e.relation FROM knowledge_edges e JOIN knowledge_nodes n ON n.id=e.target WHERE e.chunk_id=? AND e.relation<>'contains' LIMIT 12", (chunk_id,)).fetchall()
                result = {**self._chunk_record(row, source), "score": scores[chunk_id], "related": [dict(r) for r in concepts]}
                if semantic_error:
                    result["semantic_warning"] = semantic_error
                results.append(result)
                if len(results) >= limit:
                    break
        return results

    def search_for_project(self, query: str, project_id, limit: int = 10) -> list[dict]:
        """Opt-in scoped retrieval: shared material plus this project's sources only."""
        return self.search(query, limit, project_id=project_id)

    def graph(self, limit: int = 250, project_id="*", query: str = "") -> dict:
        limit = max(10, min(int(limit), 1000))
        scope_sql = "" if project_id == "*" else " AND (json_extract(data, '$.project_id') IS NULL OR json_extract(data, '$.project_id')=?)"
        scope_params = () if project_id == "*" else (project_id,)
        # Filter provenance before limiting so another project's corpus cannot
        # leak through shared concept nodes or displace the selected evidence.
        eligible = """WITH allowed_sources AS (
            SELECT id FROM entities WHERE kind='source'""" + scope_sql + """
        ), allowed_edges AS (
            SELECT * FROM knowledge_edges WHERE source_id IN (SELECT id FROM allowed_sources)
        ), eligible AS (
            SELECT * FROM knowledge_nodes n WHERE (
                n.id IN (SELECT id FROM allowed_sources)
                OR json_extract(n.data, '$.source_id') IN (SELECT id FROM allowed_sources)
                OR (json_extract(n.data, '$.source_id') IS NULL AND (
                    n.id IN (SELECT source FROM allowed_edges)
                    OR n.id IN (SELECT target FROM allowed_edges)))
            ) AND coalesce(json_extract(n.data,'$.active'),1)=1 AND instr(lower(n.label), lower(?)) > 0
        ) """
        params = (*scope_params, _text(query)[:300])
        with self.store.connection() as db:
            count = db.execute(eligible + "SELECT count(*) FROM eligible", params).fetchone()[0]
            rows = db.execute(eligible + """SELECT * FROM eligible ORDER BY CASE kind
                WHEN 'observation' THEN 0 WHEN 'source' THEN 1 WHEN 'example' THEN 3 ELSE 2 END,
                label, id LIMIT ?""", (*params, limit)).fetchall()
            nodes = [{"id": r["id"], "kind": r["kind"], "type": r["kind"], "label": r["label"], **json.loads(r["data"])} for r in rows]
            ids = {n["id"] for n in nodes}
            edges = [{"id": r["id"], "source": r["source"], "target": r["target"], "relation": r["relation"], "label": r["relation"], "source_id": r["source_id"], "chunk_id": r["chunk_id"], **json.loads(r["data"])}
                     for r in db.execute(eligible + "SELECT * FROM allowed_edges", params) if r["source"] in ids and r["target"] in ids]
        return {"nodes": nodes, "edges": edges, "total_nodes": count, "truncated": count > limit,
                "description": "Imported examples and source-backed relationships. Lexical and model-inferred links are labeled; they are not confirmed vulnerabilities."}

    def correct(self, id: str, text: str) -> dict:
        text = _text(text)
        if not text or len(text) > 20000:
            raise ValueError("A correction must contain 1–20,000 characters.")
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT * FROM knowledge_chunks WHERE id=?", (id,)).fetchone()
            if not row:
                raise ValueError("Select a source chunk to correct.")
            source_row = db.execute("SELECT data FROM entities WHERE id=? AND kind='source'", (row["source_id"],)).fetchone()
            if not source_row:
                raise ValueError("The source no longer exists.")
            source = json.loads(source_row[0])
            correction_id = new_id("correction")
            db.execute("INSERT INTO knowledge_corrections VALUES(?,?,?,?,?)", (correction_id, id, row["text"], text, now()))
            db.execute("UPDATE knowledge_chunks SET text=?,corrected=1 WHERE id=?", (text, id))
            self._invalidate_examples(db,id)
            db.execute("DELETE FROM knowledge_fts WHERE id=?", (id,))
            db.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)", (id, row["source_id"], text, _fts_title(source["title"], json.loads(row["metadata"]).get("section", "")), row["locator"]))
            db.execute("DELETE FROM knowledge_vectors WHERE chunk_id=?", (id,))
            db.execute("DELETE FROM knowledge_edges WHERE chunk_id=? AND relation<>'contains'", (id,))
            self._node(db, id, "example", text[:90], {"source_id": row["source_id"], "chunk_id": id, "locator": row["locator"], "status": "corrected"})
            self._lexical_graph(db, row["source_id"], id, text, row["locator"])
            self._remove_orphan_nodes(db)
        self._update(source["id"], semantic_index="stale", semantic_error="A correction requires rebuilding this source's semantic index.")
        self.store.event("knowledge.corrected", {"chunk_id": id, "correction_id": correction_id})
        return {"id": id, "text": text, "status": "corrected", "correction_id": correction_id, "source_id": source["id"], "locator": row["locator"]}

    def enrich(self, source_id: str, assertions: list[dict], provenance: dict | None = None) -> dict:
        """Accept model-reviewed graph suggestions with quotes, never executable tools.

        provenance records which model produced the assertion (provider/model/run)
        so the Library can show what each model has learned."""
        if not isinstance(assertions, list) or len(assertions) > 100:
            raise ValueError("Provide at most 100 source-backed assertions.")
        if not self.store.get("source", source_id):
            raise ValueError("Source not found.")
        learned_by = {k: str(v)[:80] for k, v in (provenance or {}).items()
                      if k in {"provider", "model", "run_id"} and v} or None
        accepted = []
        with self.store.connection(immediate=True) as db:
            for assertion in assertions:
                chunk = db.execute("SELECT * FROM knowledge_chunks WHERE id=? AND source_id=?", (assertion.get("chunk_id"), source_id)).fetchone()
                quote = _text(assertion.get("quote"))
                label = _text(assertion.get("text"))
                kind = assertion.get("kind", "technique")
                if not chunk or not json.loads(chunk["metadata"]).get("active",True) or not quote or quote not in chunk["text"] or not label or len(label) > 3000:
                    raise ValueError("Every assertion needs an existing source chunk and an exact quotation from its active text.")
                if kind not in {"vulnerability", "technique", "prerequisite", "tool", "example"}:
                    raise ValueError("Unsupported knowledge assertion type.")
                id = f"assertion_{_digest(source_id + chunk['id'] + kind + label)[:24]}"
                data = {"source_id": source_id, "chunk_id": chunk["id"], "status": "inferred", "origin": "model", "quote": quote, "locator": chunk["locator"]}
                if learned_by:
                    data["learned_by"] = learned_by
                    data["learned_at"] = now()
                self._node(db, id, kind, label, data)
                self._edge(db, chunk["id"], id, "supports", source_id, chunk["id"], {"origin": "model", "status": "inferred", "quote": quote})
                accepted.append(id)
        return {"source_id": source_id, "assertion_ids": accepted, "status": "inferred"}

    def learnings(self, limit: int = 300) -> dict:
        """Aggregate what every model has learned: source-backed assertions grouped
        by kind, with per-model provenance. Read-only; powers the Library view."""
        limit = max(10, min(int(limit), 1000))
        groups: dict[str, list] = {"technique": [], "vulnerability": [], "tool": [], "prerequisite": [], "example": []}
        models: dict[str, int] = {}
        with self.store.connection() as db:
            rows = db.execute("SELECT id,kind,label,data FROM knowledge_nodes WHERE coalesce(json_extract(data,'$.active'),1)=1 AND (json_extract(data,'$.chunk_id') IS NOT NULL OR id IN (SELECT target FROM knowledge_edges)) AND kind IN ('technique','vulnerability','tool','prerequisite','example') ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            for row in rows:
                data = json.loads(row["data"])
                source = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (data.get("source_id"),)).fetchone()
                source_title = json.loads(source[0]).get("title", "Source") if source else "Source"
                learned_by = data.get("learned_by") or {}
                model_label = learned_by.get("model") or ("desktop review" if data.get("origin") == "reviewed_investigation" else "unattributed")
                models[model_label] = models.get(model_label, 0) + 1
                groups.setdefault(row["kind"], []).append({
                    "id": row["id"], "kind": row["kind"], "label": row["label"],
                    "quote": data.get("quote"), "locator": data.get("locator"),
                    "source_id": data.get("source_id"), "source_title": source_title,
                    "status": data.get("status", "inferred"),
                    "model": learned_by.get("model"), "provider": learned_by.get("provider"),
                    "learned_at": data.get("learned_at")})
        return {"groups": groups, "models": models,
                "total": sum(len(v) for v in groups.values()),
                "note": "Source-backed assertions recorded by models during study and autonomous runs. Inferred unless reviewed; quotes are verbatim from the cited source."}

    def list_frames(self, source_id: str) -> list[dict]:
        source = self.store.get("source", source_id)
        if not source:
            raise ValueError("Source not found.")
        return [{"artifact_id": f.get("artifact_id", Path(f["path"]).name),
                 "time": f["time"], "locator": f["locator"]} for f in source.get("frames", [])]

    def get_frame(self, source_id: str, artifact_id: str) -> dict:
        """Resolve only registered frame artifacts; never accept an arbitrary path."""
        source = self.store.get("source", source_id)
        if not source or not isinstance(artifact_id,str):
            raise ValueError("Source frame not found.")
        frame = next((f for f in source.get("frames", []) if f.get("artifact_id",Path(f["path"]).name) == artifact_id), None)
        root = (self.store.root / "artifacts" / source_id).resolve()
        path = (root / artifact_id).resolve()
        if not frame or not path.is_relative_to(root) or path != Path(frame["path"]).resolve() or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Source frame not found.")
        return {"path": str(path), "artifact_id": artifact_id, "source_id": source_id,
                "time": frame["time"], "locator": frame["locator"], "media_type": "image/jpeg"}

    def interpret_frame(self, source_id: str, artifact_id: str, text: str) -> dict:
        """Save an explicitly labeled model interpretation with its visual source."""
        frame = self.get_frame(source_id, artifact_id)
        text = _text(text)
        if not text or len(text) > 10000:
            raise ValueError("A frame interpretation must contain 1–10,000 characters.")
        source = self.store.get("source", source_id)
        id = f"chunk_{_digest(source_id + artifact_id + 'vision')[:24]}"
        metadata = {"start": frame["time"], "frame_path": frame["path"], "artifact_id": artifact_id,
                    "assertion_status": "inferred", "origin": "model"}
        with self.store.connection(immediate=True) as db:
            if not db.execute("SELECT 1 FROM entities WHERE kind='source' AND id=?", (source_id,)).fetchone():
                raise ValueError("Source no longer exists.")
            old = db.execute("SELECT text,ordinal,metadata FROM knowledge_chunks WHERE id=?", (id,)).fetchone()
            if old and not json.loads(old["metadata"]).get("active",True):
                raise ValueError("Rejected interpretation requires a new evidence generation.")
            ordinal = old["ordinal"] if old else db.execute("SELECT coalesce(max(ordinal),-1)+1 FROM knowledge_chunks WHERE source_id=?", (source_id,)).fetchone()[0]
            if old and old["text"] != text:
                self._invalidate_examples(db, id)
            # All derived links must refer to the current interpretation. Keep
            # unrelated incoming temporal links; this frame's outgoing links rebuild below.
            db.execute("DELETE FROM knowledge_edges WHERE chunk_id=?", (id,))
            db.execute("INSERT OR REPLACE INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,0)",
                       (id, source_id, ordinal, text, text, frame["locator"], "vision_interpretation", json.dumps(metadata)))
            db.execute("DELETE FROM knowledge_fts WHERE id=?", (id,))
            db.execute("DELETE FROM knowledge_vectors WHERE chunk_id=?", (id,))
            db.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)", (id, source_id, text, source["title"], frame["locator"]))
            self._node(db, id, "example", text[:90], {"source_id": source_id, "chunk_id": id,
                       "locator": frame["locator"], "status": "inferred", "origin": "model", "artifact_id": artifact_id})
            self._edge(db, source_id, id, "visually_interpreted", source_id, id,
                       {"origin": "model", "status": "inferred", "artifact_id": artifact_id})
            self._lexical_graph(db, source_id, id, text, frame["locator"])
            self._remove_orphan_nodes(db)
            count = db.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id=?", (source_id,)).fetchone()[0]
        self._update(source_id, chunk_count=count, semantic_index="stale")
        return {"id": id, "source_id": source_id, "artifact_id": artifact_id, "status": "inferred", "locator": frame["locator"]}

    @staticmethod
    def _remove_orphan_nodes(db):
        db.execute("DELETE FROM knowledge_nodes WHERE kind<>'source' AND coalesce(json_extract(data,'$.active'),1)=1 AND id NOT IN (SELECT source FROM knowledge_edges UNION SELECT target FROM knowledge_edges)")

    def delete_source(self, id: str) -> dict:
        source = self.store.get("source", id)
        if not source:
            raise ValueError("Source not found.")
        with self.store.connection(immediate=True) as db:
            db.execute("DELETE FROM knowledge_vectors WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE source_id=?)", (id,))
            db.execute("DELETE FROM knowledge_corrections WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE source_id=?)", (id,))
            db.execute("DELETE FROM knowledge_fts WHERE source_id=?", (id,))
            db.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (id,))
            db.execute("DELETE FROM knowledge_edges WHERE source_id=?", (id,))
            db.execute("DELETE FROM knowledge_nodes WHERE id=? OR json_extract(data,'$.source_id')=?", (id, id))
            db.execute("DELETE FROM entities WHERE kind='source' AND id=?", (id,))
            db.execute("DELETE FROM entities WHERE kind='learning_example' AND json_extract(data,'$.source_id')=?", (id,))
            db.execute("DELETE FROM entities WHERE kind='course_procedure' AND json_extract(data,'$.source_id')=?", (id,))
            db.execute("DELETE FROM entities WHERE kind='experiment_run' AND json_extract(data,'$.source_id')=?", (id,))
            self._remove_orphan_nodes(db)
        if source.get("path"):
            path = Path(source["path"])
            if path.parent == self.store.root / "sources":
                path.unlink(missing_ok=True)
        shutil.rmtree(self.store.root / "artifacts" / id, ignore_errors=True)
        self.store.event("source.deleted", {"source_id": id})
        return {"id": id, "deleted": True}
