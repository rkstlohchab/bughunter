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

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".http", ".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".rs", ".js", ".ts", ".tsx", ".java", ".cs", ".sh", ".bat"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | MEDIA_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf", ".epub", ".html", ".htm", ".srt", ".vtt", ".zip", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp"}
MAX_FILE_BYTES = 32 * 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARS = 10_000_000

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
    def __init__(self, store: Store, start_worker: bool = True):
        self.store = store
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._queued: set[str] = set()
        self._queue_lock = threading.Lock()
        self._vec_available = False
        self._init_schema()
        from .course_p0 import CourseP0, ProcedureP0
        self.procedures = ProcedureP0(self)
        self.courses = CourseP0(self, start_worker=start_worker)
        from .experiment_p1 import ExperimentP1
        self.experiments = ExperimentP1(self, run_workers=start_worker)
        if start_worker:
            self._worker = threading.Thread(target=self._work, daemon=True, name="knowledge-ingestion")
            self._worker.start()
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
            "sources.delete": lambda: self.delete_source(p["id"]),
            "sources.retry": lambda: self.retry_source(p["id"]),
            "sources.release_original": lambda: self.portability().release_original(**p),
            "knowledge.export": lambda: self.portability().export_bundle(**p),
            "knowledge.examples.save": lambda: self.portability().save_example(**p),
            "knowledge.examples.list": lambda: self.store.list("learning_example"),
            "knowledge.search": lambda: self.search(p.get("query", ""), p.get("limit", 10), project_id=p.get("project_id", "*")),
            "knowledge.graph": lambda: self.graph(p.get("limit", 250)),
            "knowledge.learnings": lambda: self.learnings(p.get("limit", 300)),
            "knowledge.correct": lambda: self.correct(p["id"], p["text"]),
            "knowledge.enrich": lambda: self.enrich(p["source_id"], p["assertions"]),
            "knowledge.frames": lambda: self.list_frames(p["source_id"]),
            "knowledge.status": lambda: self.status(),
            "knowledge.integrity": lambda: self.integrity(p.get("project_id", "*")),
            "courses.import": lambda: self.courses.import_archive(p["path"],p.get("project_id")),
            "courses.list": lambda: self.courses.list_batches(p.get("project_id","*")),
            "courses.status": lambda: self.courses.status(p["id"]),
            "courses.resume": lambda: self.courses.resume(p["id"]),
            "knowledge.procedures": lambda: self.procedures.list(p.get("source_id"),p.get("project_id","*"),p.get("limit",150)),
            "knowledge.procedures.get": lambda: self.procedures.get(p["id"]),
            "knowledge.procedures.rebuild": lambda: self.procedures.build(p["source_id"]),
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
                "worker_running": bool(self._worker and self._worker.is_alive()),
                "queue_size": self._queue.qsize(),
                "extractors": {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "whisper-cli", "tesseract")}}

    def integrity(self, project_id="*") -> dict:
        """Inspect a consistent local snapshot without repairing it or using a model.

        Scope is applied inside every query. Results contain counts, never private
        source titles, passages or identifiers from another project.
        """
        from .security import redact_text

        scope = "" if project_id == "*" else " AND (json_extract(data,'$.project_id') IS NULL OR json_extract(data,'$.project_id')=?)"
        params = () if project_id == "*" else (project_id,)
        selected = """WITH selected_sources AS (
            SELECT id,data FROM entities WHERE kind='source'""" + scope + """),
            selected_chunks AS (SELECT c.* FROM knowledge_chunks c
                JOIN selected_sources s ON s.id=c.source_id) """
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
             "SELECT count(*) FROM knowledge_nodes n JOIN selected_sources s ON s.id=json_extract(n.data,'$.source_id') LEFT JOIN knowledge_chunks c ON c.id=json_extract(n.data,'$.chunk_id') AND c.source_id=s.id WHERE n.id LIKE 'assertion_%' AND (c.id IS NULL OR coalesce(json_extract(n.data,'$.quote'),'')='' OR instr(c.text,json_extract(n.data,'$.quote'))=0)"),
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
        return {**source, "chunks": [self._chunk_record(r, source) for r in rows], "corrections": [dict(r) for r in correction_rows]}

    def _import_youtube_collection(self, url: str, language: str = "en", download_media: bool = False, project_id=None) -> dict:
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
                language=language, download_media=download_media, project_id=project_id))
            if len(records) >= 50:
                break
        if not records:
            raise ValueError("The collection did not expose video IDs for import.")
        return {"id": "batch_youtube_" + _digest(url)[:18],
                "title": str(listing.get("title") or "YouTube collection")[:150],
                "status": "queued", "source_ids": [r["id"] for r in records],
                "sources": records, "count": len(records), "limited_to_first": 50,
                "message": "Queued up to 50 public videos. Import another batch later for larger channels."}

    def import_source(self, path: str | None = None, filename: str | None = None,
                      content_base64: str | None = None, url: str | None = None,
                      download_media: bool = False, language: str = "en", copy_original: bool = True, project_id=None) -> dict:
        if project_id is not None and not self.store.get("project", project_id):
            raise ValueError("Unknown knowledge project.")
        if sum(v is not None for v in (path, content_base64, url)) != 1:
            raise ValueError("Provide one local path, uploaded file, or website/video URL.")
        if url:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            if host not in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"}:
                url = canonical_url(url)
                key = _digest("web:"+url)
                scoped_key = _digest(key + ":" + project_id) if project_id else key
                id = f"source_{scoped_key[:24]}"
                if existing := self.store.get("source",id): return {**existing,"duplicate":True}
                source = self.store.put("source",{"id":id,"title":host+(urlparse(url).path or "/"),"url":url,"web_resource":True,
                    "filename":Path(urlparse(url).path).name or "index.html","media_type":"website","content_hash":key,
                    "status":"queued","chunk_count":0,"error":None,"warnings":[],"semantic_index":"pending",
                    "project_id": project_id})
                self._schedule(id)
                return source
            try:
                video_id = youtube_video_id(url)
            except ValueError:
                return self._import_youtube_collection(url, language, download_media, project_id=project_id)
            if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", language):
                raise ValueError("Use a language code such as en or hi.")
            key = _digest(f"youtube:{video_id}:{language}")
            scoped_key = _digest(key + ":" + project_id) if project_id else key
            id = f"source_{scoped_key[:24]}"
            existing = self.store.get("source", id)
            if existing:
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
                "project_id": project_id})
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
                sources = [self.import_source(path=str(p), copy_original=copy_original, project_id=project_id) for p in candidates]
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
            "warnings": [], "error": None, "semantic_index": "pending", "project_id": project_id})
        if existing:
            (self.store.root/"artifacts"/id/"extracted.json").unlink(missing_ok=True)
        self._schedule(id)
        self.store.event("source.queued", {"source_id": id})
        return source

    def _schedule(self, id: str):
        with self._queue_lock:
            if id not in self._queued:
                self._queued.add(id)
                self._queue.put(id)

    def _update(self, id: str, **changes) -> dict | None:
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (id,)).fetchone()
            if not row:
                return None
            source = {**json.loads(row[0]), **changes, "updated_at": now()}
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=? AND kind='source'", (json.dumps(source), source["updated_at"], id))
            return source

    def resume_pending(self):
        for source in self.list_sources():
            if source["status"] == "queued":
                self._schedule(source["id"])
            elif source["status"] == "processing" and not self._pid_alive(source.get("worker_pid")):
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

    def _claim(self, id: str) -> dict | None:
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE id=? AND kind='source'", (id,)).fetchone()
            if not row:
                return None
            source = json.loads(row[0])
            if source["status"] != "queued":
                return None
            source.update(status="processing", stage="extracting", error=None, worker_pid=os.getpid(), updated_at=now())
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?", (json.dumps(source), source["updated_at"], id))
            return source

    def _work(self):
        while not self._stop.is_set():
            try:
                id = self._queue.get(timeout=0.5)
            except queue.Empty:
                self.resume_pending()
                continue
            if id is None:
                break
            try:
                self.process_source(id)
            finally:
                with self._queue_lock:
                    self._queued.discard(id)
                self._queue.task_done()

    def process_source(self, id: str) -> dict | None:
        """Process a queued source synchronously (also useful for deterministic tests)."""
        source = self._claim(id)
        if not source:
            return self.store.get("source", id)
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
            source = self._update(id, stage="indexing")
            if not source:
                return None
            count = self._index(source, segments)
            if count:
                try:
                    self.procedures.build(id)
                except Exception as exc:  # Procedure candidates cannot invalidate the source.
                    warnings.append(f"Procedure extraction unavailable: {str(exc)[:160]}")
            if count == 0:
                warnings.append("No searchable text was extracted. Check extractor/model availability and retry.")
            semantic_status = "unavailable"
            semantic_error = None
            if count:
                try:
                    semantic_status = self._index_vectors(id)
                except Exception as exc:  # noqa: BLE001 - optional model failures must preserve keyword search
                    semantic_error = str(exc)[:350]
            result = self._update(id, status="partial" if warnings else "ready", stage="complete",
                                 warnings=list(dict.fromkeys(warnings)), chunk_count=count,
                                 semantic_index=semantic_status, semantic_error=semantic_error, worker_pid=None)
            self.store.event("source.indexed", {"source_id": id, "chunk_count": count,
                                                "status": result["status"] if result else "deleted"})
            return result
        except Exception as exc:  # noqa: BLE001 - durable worker boundary records extractor failures
            result = self._update(id, status="error", stage="failed", error=str(exc)[:1000], worker_pid=None)
            self.store.event("source.failed", {"source_id": id, "error": str(exc)[:400]})
            return result
        finally:
            # A user may delete a source while an extractor is still running. Do
            # not leave late-created frames/checkpoints behind or recreate it.
            if self.store.get("source", id) is None:
                shutil.rmtree(self.store.root / "artifacts" / id, ignore_errors=True)

    def retry_source(self, id: str) -> dict:
        with self.store.connection(immediate=True) as db:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?",(id,)).fetchone()
            if not row: raise ValueError("Source not found.")
            source = json.loads(row[0])
            if source.get("original_retained") is False and not source.get("url"):
                raise ValueError("The original was released. Import the original file again to re-extract it; existing knowledge is still available.")
            if source["status"] == "processing" and self._pid_alive(source.get("worker_pid")):
                raise ValueError("This source is still processing.")
            source.update(status="queued",error=None,worker_pid=None,original_retained=True)
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE id=?",(json.dumps(source),now(),id))
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
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=2)

    def _extract(self, source: dict, artifacts: Path, budget=None, depth=0) -> tuple[list[dict], list[str]]:
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
            return self._extract_pdf(path)
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
            raise ValueError("Executable binary detected. Import its source code or accompanying documentation; binaries are never executed.")
        raise ValueError("No parser is available for this binary/format. Export to PDF, a modern Office format, text, or a regular ZIP archive.")

    def _extract_website(self, source, artifacts):
        snapshot = artifacts / "downloaded-resource"
        receipt = fetch_resource(source["url"],snapshot,lambda: self._stop.is_set() or self.store.get("source",source["id"]) is None)
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
        config = runtime.config() if runtime else {}
        if runtime and config.get("enabled", True) and config.get("vision_enabled", True):
            try:
                description = runtime.vision(frame, 0)
                if description:
                    segments.append({"text":description,"locator":"Visual analysis","modality":"vision",
                                     "frame_path":str(frame),"model":config["vision_model"]})
            except (ValueError, OSError, httpx.HTTPError) as exc:
                warnings.append(f"Qwen3-VL visual analysis unavailable ({type(exc).__name__}); set it up in Video AI and reprocess.")
        else:
            warnings.append("Visual model analysis disabled or unavailable; use Video AI setup to enable it.")
        self._update(source["id"],frames=[{"artifact_id":frame.name,"path":str(frame),"time":0,"locator":"Image"}],frame_count=1)
        return segments,warnings

    def _extract_pdf(self, path: Path) -> tuple[list[dict], list[str]]:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("This PDF is encrypted; import an unlocked copy.")
        segments, warnings = [], []
        for i, page in enumerate(reader.pages):
            text = _text(page.extract_text())
            modality = "text"
            if len(text) < 20:
                try:
                    import pymupdf
                    import pytesseract
                    from PIL import Image
                    with pymupdf.open(path) as pdf:
                        bitmap = pdf[i].get_pixmap(matrix=pymupdf.Matrix(2, 2))
                        text = pytesseract.image_to_string(Image.frombytes("RGB", [bitmap.width, bitmap.height], bitmap.samples))
                    modality = "ocr"
                except (ImportError, OSError, RuntimeError) as exc:
                    warnings.append(f"Page {i + 1} needs OCR: {type(exc).__name__}. Install the OCR extras and Tesseract.")
            if _text(text):
                segments.append({"text": text, "locator": f"Page {i + 1}", "modality": modality, "page": i + 1})
            else:
                warnings.append(f"Page {i + 1} contains no extracted text.")
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

    @staticmethod
    def _run(arguments: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
        if result.returncode:
            raise ValueError(f"{Path(arguments[0]).name} failed: {result.stderr[-600:]}")
        return result

    def _extract_media(self, path: Path, artifacts: Path, source: dict, transcript: list[dict] | None = None) -> tuple[list[dict], list[str]]:
        """Checkpointed local frame + Whisper analysis, preserving timestamp provenance.

        Speech segments and screenshots are separate evidence sources; the index
        connects them chronologically but never claims that a sampled frame
        proves a click occurred between frames.
        """
        from .local_media import binary
        runtime = getattr(self, "media_runtime", None)
        settings = runtime.config() if runtime else {"enabled": False, "frame_interval": 30, "max_frames": 48}
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
        segments, warnings = list(transcript or []), []
        artifacts.mkdir(parents=True, exist_ok=True)
        sid = source["id"]
        timeline_path = artifacts / "media_timeline.json"
        timeline = json.loads(timeline_path.read_text()) if timeline_path.exists() else []
        speech_cache = artifacts / "speech_segments.json"
        if has_audio and not transcript:
            if speech_cache.exists():
                segments.extend(json.loads(speech_cache.read_text()))
            else:
                model_path = (runtime.whisper_path() if runtime else
                              Path(self._settings().get("whisper_model_path") or os.environ.get("BUGHUNTER_WHISPER_MODEL") or ""))
                whisper = binary("whisper-cli")
                if runtime and not settings.get("speech_enabled", True):
                    warnings.append("Local speech transcription disabled in Video AI setup.")
                elif whisper and model_path.is_file():
                    if runtime: runtime.checkpoint(sid, self)
                    audio = artifacts / "audio.wav"
                    if not audio.exists():
                        self._run([ffmpeg, "-nostdin", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)], 3600)
                    output = artifacts / "transcript"
                    if not output.with_suffix(".srt").exists():
                        self._run([whisper, "-m", str(model_path), "-f", str(audio),
                                   "-osrt", "-of", str(output), "-l", "auto", "-nt"], 7200)
                    if output.with_suffix(".srt").exists():
                        speech = self._subtitle_segments(output.with_suffix(".srt").read_text())
                        segments.extend(speech)
                        speech_cache.write_text(json.dumps(speech))
                    # Audio is a large intermediate. The SRT is a durable checkpoint.
                    audio.unlink(missing_ok=True)
                else:
                    warnings.append("Local Whisper is not ready. In Video AI, install whisper.cpp and the speech model, then reprocess.")
        if has_video:
            interval = float(settings.get("frame_interval", 30))
            cap = int(settings.get("max_frames", 0))
            from .media_sampling import scene_cuts, plan_frames
            # Sample every configured interval plus moments where the visible UI
            # changes sharply. A scene cut is NOT proof a button was clicked.
            extra_times = []
            if settings.get("adaptive_frames", True) and settings.get("vision_enabled",True) and duration:
                extra_times = scene_cuts(ffmpeg, str(path), duration,
                    threshold=float(settings.get("scene_threshold",0.30)))
            stamps = plan_frames(duration, interval, cap, extra_times)
            total_frames = len(stamps)
            step = interval
            frames = []
            existing = {round(float(t.get("start", -1)), 2): t for t in timeline if t.get("modality") == "vision"}
            vision_usable = bool(runtime and settings.get("enabled", True) and settings.get("vision_enabled", True))
            had_model_error = False
            for index, stamp in enumerate(stamps):
                if runtime: runtime.checkpoint(sid, self)
                # Timestamp-keyed paths avoid silently reusing the wrong JPEG
                # when the interval or frame cap changes between reprocesses.
                frame = artifacts / f"frame-{int(round(stamp * 1000)):012d}.jpg"
                if not frame.exists():
                    self._run([ffmpeg, "-nostdin", "-y", "-ss", str(stamp), "-i", str(path), "-frames:v", "1",
                               "-vf", "scale='min(1000,iw)':-2", "-q:v", "5", str(frame)], 90)
                if not frame.exists():
                    continue
                frames.append({"artifact_id":frame.name,"path":str(frame),"time":stamp,
                               "locator":f"Frame at {_seconds_label(stamp)}"})
                visual = existing.get(round(stamp, 2))
                if visual is None and vision_usable:
                    try:
                        description = runtime.vision(frame, stamp)
                        if description:
                            visual = {"text":description,"locator":f"Frame at {_seconds_label(stamp)}",
                                      "modality":"vision","start":stamp,"end":min(duration,stamp+step),
                                      "frame_path":str(frame),"model":settings.get("vision_model")}
                            timeline.append(visual)
                            existing[round(stamp, 2)] = visual
                            # Atomic checkpoints enable retry after power loss or interruption.
                            tmp = timeline_path.with_suffix(".tmp")
                            tmp.write_text(json.dumps(timeline,ensure_ascii=False))
                            tmp.replace(timeline_path)
                    except (ValueError, OSError, httpx.HTTPError) as exc:
                        if not had_model_error:
                            warnings.append(f"Vision not available ({type(exc).__name__}); frames preserved for reprocessing.")
                        had_model_error = True
                        vision_usable = False
                if visual:
                    segments.append(visual)
                else:
                    try:
                        import pytesseract
                        from PIL import Image
                        text = _text(pytesseract.image_to_string(Image.open(frame)))
                        if text:
                            segments.append({"text":text,"locator":f"Frame at {_seconds_label(stamp)}",
                                             "modality":"ocr","start":stamp,"frame_path":str(frame)})
                    except (ImportError, OSError, RuntimeError):
                        pass
                if index % 5 == 0:
                    self._update(sid, frame_count=len(frames), media_progress={"analyzed":index+1,"total":total_frames,"timestamp":stamp}, duration=duration)
            (artifacts / "frames.json").write_text(json.dumps(frames))
            self._update(sid, frame_count=len(frames), frames=frames, duration=duration,
                         media_progress={"analyzed":len(frames),"total":total_frames,"timestamp":duration})
            if not vision_usable and not existing:
                warnings.append("Only sampled frames and available OCR were indexed. Enable Qwen3-VL for frame descriptions.")
            warnings.append("Visual timestamps indicate sampled frames/scene changes, not verified clicks; actions can fall between samples.")
        elif not has_audio:
            warnings.append("No audio or video stream was detected.")
        if segments:
            # Timeline includes both modalities and is searchable via ordinary source chunks.
            ordered = sorted(segments, key=lambda s:(float(s.get("start",0)), 0 if s.get("modality")=="transcript" else 1))
            tmp = timeline_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(ordered,ensure_ascii=False))
            tmp.replace(timeline_path)
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

    def _index(self, source: dict, segments: list[dict]) -> int:
        id = source["id"]
        records = []
        seen = set()
        for segment in segments:
            for index, (text, section) in enumerate(_sectioned_chunks(segment.get("text", ""))):
                locator = segment.get("locator", "Document")
                chunk_id = f"chunk_{_digest(id + locator + str(index) + text)[:24]}"
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                metadata = {k: v for k, v in segment.items() if k not in {"text", "locator", "modality"}}
                if section:
                    metadata["section"] = section
                records.append({"id": chunk_id, "text": text, "locator": locator,
                                "modality": segment.get("modality", "text"), "metadata": metadata})
        with self.store.connection(immediate=True) as db:
            if not db.execute("SELECT 1 FROM entities WHERE kind='source' AND id=?", (id,)).fetchone():
                return 0
            existing = {r["id"]: dict(r) for r in db.execute("SELECT * FROM knowledge_chunks WHERE source_id=?", (id,))}
            valid_ids = {r["id"] for r in records}
            for old_id in existing.keys() - valid_ids:
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
                db.execute("INSERT OR REPLACE INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,?)", (
                    record["id"], id, ordinal, text, record["text"], record["locator"], record["modality"], json.dumps(record["metadata"]), corrected))
                db.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?,?)", (record["id"], id, text, _fts_title(source["title"], record["metadata"].get("section", "")), record["locator"]))
                self._node(db, record["id"], "example", text[:90], {"source_id": id, "chunk_id": record["id"], "locator": record["locator"],
                    "status": "corrected" if corrected else "imported", "modality": record["modality"]})
                self._edge(db, id, record["id"], "contains", id, record["id"], {"origin": "lexical", "status": "imported"})
                self._lexical_graph(db, id, record["id"], text, record["locator"])
            # Evidence-backed temporal edges between indexed transcript/vision chunks.
            timed = [(float(r["metadata"]["start"]), r["id"]) for r in records if isinstance(r["metadata"].get("start"), (int, float))]
            timed.sort(key=lambda pair: (pair[0], pair[1]))
            for (at, earlier), (next_at, later) in zip(timed, timed[1:]):
                if earlier != later:
                    self._edge(db, earlier, later, "precedes", id, earlier,
                               {"origin":"timestamp", "start":at, "next_start":next_at, "status":"observed_order"})
            self._remove_orphan_nodes(db)
        return len(records)

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
            chunks = db.execute("SELECT id,text FROM knowledge_chunks WHERE source_id=? ORDER BY ordinal", (source_id,)).fetchall()
        if not chunks:
            return "empty"
        # No simultaneous large models: one serialized worker, one small embedding batch.
        for offset in range(0, len(chunks), 16):
            batch = chunks[offset:offset + 16]
            model, vectors = self._embed([c["text"] for c in batch])
            with self.store.connection(immediate=True) as db:
                for chunk, vector in zip(batch, vectors):
                    current = db.execute("SELECT text FROM knowledge_chunks WHERE id=?", (chunk["id"],)).fetchone()
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
                WHERE knowledge_fts MATCH ?""" + scope_sql + " ORDER BY rank LIMIT ?",
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
                            WHERE v.model=? AND v.dimension=?""" + scope_sql + " ORDER BY distance LIMIT ?",
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

    def graph(self, limit: int = 250) -> dict:
        limit = max(10, min(int(limit), 1000))
        with self.store.connection() as db:
            count = db.execute("SELECT count(*) FROM knowledge_nodes").fetchone()[0]
            rows = db.execute("SELECT * FROM knowledge_nodes ORDER BY CASE kind WHEN 'source' THEN 0 WHEN 'example' THEN 2 ELSE 1 END,label LIMIT ?", (limit,)).fetchall()
            nodes = [{"id": r["id"], "kind": r["kind"], "type": r["kind"], "label": r["label"], **json.loads(r["data"])} for r in rows]
            ids = {n["id"] for n in nodes}
            edges = [{"id": r["id"], "source": r["source"], "target": r["target"], "relation": r["relation"], "label": r["relation"], "source_id": r["source_id"], "chunk_id": r["chunk_id"], **json.loads(r["data"])}
                     for r in db.execute("SELECT * FROM knowledge_edges") if r["source"] in ids and r["target"] in ids]
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
                if not chunk or not quote or quote not in chunk["text"] or not label or len(label) > 3000:
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
            rows = db.execute("SELECT id,kind,label,data FROM knowledge_nodes WHERE kind IN ('technique','vulnerability','tool','prerequisite','example') ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
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
            old = db.execute("SELECT text,ordinal FROM knowledge_chunks WHERE id=?", (id,)).fetchone()
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
        db.execute("DELETE FROM knowledge_nodes WHERE kind<>'source' AND id NOT IN (SELECT source FROM knowledge_edges UNION SELECT target FROM knowledge_edges)")

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
