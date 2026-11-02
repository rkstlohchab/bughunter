"""Offline-recoverable, authenticated-by-hash snapshots of a workbench data directory.

Archives are PRIVATE but not encrypted. Never put them on an untrusted cloud drive.
Restore refuses active desktop leases, symbolic links, overwrites, and corrupt entries.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path, PurePosixPath

from .file_lock import try_lock

SCHEMA = 1
FOLDERS = ("sources", "artifacts", "exports", "workspaces")
MAX_FILES = 20000
MAX_UNCOMPRESSED = 8 * 1024**3
CHUNK = 1024 * 1024


def digest_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path):
    yield root / "workbench.sqlite3", "workbench.sqlite3"
    for folder in FOLDERS:
        base = root / folder
        if base.is_symlink():
            raise ValueError("Data directory contains a symlink")
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlink in data directory: {path.relative_to(root)}")
            if path.is_file():
                yield path, path.relative_to(root).as_posix()


def _safe_name(name):
    p = PurePosixPath(name)
    return bool(name and not name.startswith("/") and "\\" not in name and
                len(name) <= 1024 and p.parts and all(x not in (".", "..", "") for x in p.parts) and
                str(p) == name and (name == "workbench.sqlite3" or p.parts[0] in FOLDERS))


def _validate_db(path: Path):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database failed SQLite integrity check")
        entities = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if "entities" not in {e[0] for e in entities}:
            raise ValueError("Missing entities table")


def create(root: str | Path, destination: str | Path) -> dict:
    root, dest = Path(root).resolve(), Path(destination).expanduser().resolve()
    if (root / "workbench.sqlite3").is_symlink():
        raise ValueError("Database cannot be a symlink")
    if not (root / "workbench.sqlite3").is_file():
        raise FileNotFoundError("No workbench database found")
    if dest.is_relative_to(root):
        raise ValueError("Write backups outside the live data directory")
    if dest.exists():
        raise FileExistsError("Backup already exists; choose a new filename")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bughunter-backup-") as tmp:
        tmp = Path(tmp)
        db_copy = tmp / "workbench.sqlite3"
        with closing(sqlite3.connect(root / "workbench.sqlite3")) as src, closing(sqlite3.connect(db_copy)) as target:
            src.backup(target)  # includes committed WAL changes
        _validate_db(db_copy)
        files = [(db_copy, "workbench.sqlite3"), *list(_files(root))[1:]]
        manifest = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
                    "original_data_root": str(root),
                    "warning": "Unencrypted private research archive. OS keychain credentials are not included.",
                    "files": {}}
        total = 0
        for path, name in files:
            if not _safe_name(name) or not path.is_file() or path.is_symlink():
                raise ValueError("Unsafe archive source")
            size = path.stat().st_size
            total += size
            if total > MAX_UNCOMPRESSED or len(manifest["files"]) >= MAX_FILES:
                raise ValueError("Backup exceeds the configured size/file limits")
            manifest["files"][name] = {"sha256": digest_file(path), "size": size}
        # Atomic publish: on failure, no partially valid backup at the requested path.
        with tempfile.NamedTemporaryFile(prefix=".bughunter-", suffix=".zip", dir=dest.parent, delete=False) as handle:
            staging = Path(handle.name)
        try:
            staging.chmod(0o600)
            with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as archive:
                for path, name in files:
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            verify(staging)
            os.replace(staging, dest)
        finally:
            staging.unlink(missing_ok=True)
    return {"path": str(dest), "files": len(files), "uncompressed_bytes": total}


def verify(archive_path: str | Path) -> dict:
    path = Path(archive_path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) < 2 or len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Archive contains missing/duplicate entries")
        if len(names) - 1 > MAX_FILES:
            raise ValueError("Archive contains too many files")
        if any((name != "manifest.json" and not _safe_name(name)) for name in names):
            raise ValueError("Archive contains unsafe paths")
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > 4 * 1024**2:
            raise ValueError("Manifest is too large")
        manifest = json.loads(archive.read("manifest.json"))
        expected = manifest.get("files", {})
        if manifest.get("schema") != SCHEMA or not isinstance(expected, dict):
            raise ValueError("Unsupported backup format")
        if set(expected) != (set(names) - {"manifest.json"}) or "workbench.sqlite3" not in expected:
            raise ValueError("Archive manifest doesn't match contents")
        if sum(i.file_size for i in archive.infolist()) > MAX_UNCOMPRESSED + 4 * 1024**2:
            raise ValueError("Archive exceeds unpacking limit")
        for info in archive.infolist():
            # Unix symlinks inside ZIPs must not be extracted.
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise ValueError("Archive contains a symbolic link")
            if info.filename == "manifest.json":
                continue
            meta = expected[info.filename]
            if type(meta.get("size")) is not int or meta["size"] != info.file_size:
                raise ValueError("File-size mismatch")
            h = hashlib.sha256()
            with archive.open(info) as inp:
                for chunk in iter(lambda: inp.read(CHUNK), b""):
                    h.update(chunk)
            if h.hexdigest() != meta.get("sha256"):
                raise ValueError("File checksum mismatch: " + info.filename)
        return {"verified": True, "files": len(expected), "uncompressed_bytes": sum(x["size"] for x in expected.values())}


def restore(archive_path: str | Path, destination: str | Path) -> dict:
    """Restore into a NEW empty directory only. Call when the app is stopped."""
    raw_destination = Path(destination).expanduser().absolute()
    if raw_destination.is_symlink():
        raise ValueError("Destination cannot be a symbolic link")
    destination = raw_destination.resolve()
    stats = verify(archive_path)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Restore destination must be a new or empty directory. Stop the app and use a new location.")
    if destination.is_symlink():
        raise ValueError("Destination cannot be a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bughunter-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            previous_root = manifest.get("original_data_root", "")
            for name in archive.namelist():
                if name == "manifest.json":
                    continue
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as inp, target.open("xb") as out:
                    shutil.copyfileobj(inp, out, CHUNK)
                target.chmod(0o600)
        _validate_db(staging / "workbench.sqlite3")
        # Only rewrite machine-local file path fields. Never rewrite freeform
        # research text, URL, prompts, memory transcripts, or external links.
        def rewrite(value):
            if isinstance(value, dict):
                return {k: (str(destination) + v[len(previous_root):]
                        if previous_root and k in {"path", "file_path", "artifact_path", "output_path"}
                        and isinstance(v, str) and v.startswith(previous_root + os.sep)
                        else rewrite(v)) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            return value
        if previous_root:
            with closing(sqlite3.connect(staging / "workbench.sqlite3")) as db:
                rows = db.execute("SELECT id,data FROM entities").fetchall()
                for entity_id, blob in rows:
                    old = json.loads(blob)
                    new = rewrite(old)
                    if new != old:
                        db.execute("UPDATE entities SET data=? WHERE id=?", (json.dumps(new), entity_id))
                db.commit()
        _validate_db(staging / "workbench.sqlite3")
        for folder in FOLDERS:
            (staging / folder).mkdir(exist_ok=True, mode=0o700)
        staging.chmod(0o700)
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**stats, "restored_to": str(destination), "note": "App keychain credentials require reconnection after restore."}
