"""Small durable store shared by desktop, workers and the policy gateway."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from .file_lock import try_lock
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix + '_' if prefix else ''}{uuid.uuid4().hex}"


class Store:
    def require_desktop_owner(self):
        """Fail closed after the desktop owner exits, including an unclean crash."""
        lock = self.root / "desktop.lock"
        # Standalone unit/lab services do not establish a desktop lease.
        if not lock.exists(): return
        with lock.open("a+") as file:
            if not try_lock(file):
                return
            raise ValueError("The desktop owner has exited. Reopen the app and review interrupted work.")

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("BUGHUNTER_DATA_DIR", Path.home() / "Library/Application Support/BugHunterClaudex")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workbench.sqlite3"
        for name in ("sources", "artifacts", "exports", "workspaces"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self, immediate: bool = False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, kind: str, data: dict, id: str | None = None) -> dict:
        entity_id = id or data.get("id") or new_id(kind)
        with self.connection(immediate=True) as db:
            project_id = entity_id if kind == "project" else data.get("project_id")
            if project_id and db.execute("SELECT 1 FROM settings WHERE key=?",("deleted_project:"+project_id,)).fetchone():
                raise ValueError("This project was deleted; create a new project to continue.")
            old = db.execute("SELECT created_at FROM entities WHERE id=? AND kind=?", (entity_id, kind)).fetchone()
            stamp = now()
            value = {**data, "id": entity_id, "created_at": old[0] if old else data.get("created_at", stamp), "updated_at": stamp}
            db.execute("INSERT INTO entities(id,kind,data,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at WHERE entities.kind=excluded.kind", (entity_id,kind,json.dumps(value),value["created_at"],stamp))
        return value

    def get(self, kind: str, id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT data FROM entities WHERE kind=? AND id=?", (kind,id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str, **filters) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT data FROM entities WHERE kind=? ORDER BY created_at DESC", (kind,)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return [v for v in values if all(v.get(k) == val for k,val in filters.items())]

    def delete(self, kind: str, id: str) -> bool:
        with self.connection() as db:
            count = db.execute("DELETE FROM entities WHERE kind=? AND id=?", (kind,id)).rowcount
        return bool(count)

    def setting(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.connection() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value)))
        return value

    def event(self, type: str, data: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO events(type,data,created_at) VALUES(?,?,?)", (type,json.dumps(data),now()))

    def events(self, after: int = 0, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after,min(limit,500))).fetchall()
        return [{**dict(r),"data":json.loads(r["data"])} for r in rows]
