"""Bounded value scans with disk-backed unknown snapshots and explicit coverage."""
from __future__ import annotations

import json
import hashlib
import math
import shutil
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ..store import new_id

_CHUNK_BYTES = 4 * 1024 * 1024
_RESIDENT_BUDGET_BYTES = 256 * 1024 * 1024
_SNAPSHOT_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
_CHECKPOINT_SECONDS = 120.0
_SCALAR_FMT = {"u8": ("<B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "u64": ("<Q", 8),
               "i8": ("<b", 1), "i16": ("<h", 2), "i32": ("<i", 4), "i64": ("<q", 8),
               "f32": ("<f", 4), "f64": ("<d", 8)}


def _file_digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class ScanCancelled(RuntimeError):
    pass


class ScanBudgetExhausted(RuntimeError):
    pass


@dataclass
class ScanProgress:
    bytes_read: int = 0
    bytes_skipped: int = 0
    regions_covered: int = 0
    regions_skipped: int = 0
    matches: int = 0
    phase: str = "idle"
    started_at: float = 0.0
    last_checkpoint_at: float = 0.0

    def as_dict(self):
        return {k: getattr(self, k) for k in ("bytes_read", "bytes_skipped", "regions_covered",
                                             "regions_skipped", "matches", "phase")}


@dataclass(slots=True)
class ScanMatch:
    address: int
    value: Any
    value_type: str


@dataclass
class ScanSession:
    adapter: Any
    value_type: str
    session_id: str = field(default_factory=lambda: new_id("scan_session"))
    progress: ScanProgress = field(default_factory=ScanProgress)
    matches: list[ScanMatch] = field(default_factory=list)
    skipped_regions: list[dict] = field(default_factory=list)
    _cancel: threading.Event = field(default_factory=threading.Event)
    _snapshot_dir: Optional[Path] = None
    _snapshot_bytes: int = 0
    _value_cache: dict = field(default_factory=dict)  # retained for caller compatibility; never populated
    _snapshots: list = field(default_factory=list)
    _unknown: bool = False
    _pattern_size: int = 0
    _reserved_bytes: int = 0
    max_read_bytes: Optional[int] = None
    max_matches: Optional[int] = None
    deadline: Optional[float] = None
    chunk_bytes: Optional[int] = None
    _incomplete_reason: str = ""
    _skipped_count: int = 0

    def cancel(self):
        self._cancel.set()

    def close(self):
        if self._snapshot_dir:
            shutil.rmtree(self._snapshot_dir, ignore_errors=True)
        self._snapshot_dir = None
        self._snapshot_bytes = 0
        self._snapshots.clear()
        self._value_cache.clear()
        self.matches.clear()
        self._unknown = False

    def set_limits(self, *, max_read_bytes=None, max_matches=None, deadline_s=None, chunk_bytes=None):
        """Set per-operation limits. Cancellation is sticky until explicitly reset."""
        if max_read_bytes is not None and max_read_bytes <= 0:
            raise ValueError("read budget must be positive")
        if max_matches is not None and max_matches <= 0:
            raise ValueError("match budget must be positive")
        if deadline_s is not None and not 0 < deadline_s <= 20:
            raise ValueError("scan deadline must be in (0, 20] seconds")
        self.max_read_bytes, self.max_matches = max_read_bytes, max_matches
        self.deadline = time.monotonic() + deadline_s if deadline_s is not None else None
        if chunk_bytes is not None and not 1 <= chunk_bytes <= _CHUNK_BYTES:
            raise ValueError("invalid scan chunk size")
        self.chunk_bytes = chunk_bytes

    def _check_limits(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.progress.phase = "time_budget_exhausted"
            self._incomplete_reason = "time_budget_exhausted"
            raise ScanBudgetExhausted("scan time budget exhausted")

    def _begin(self, phase, *, fresh=False):
        if fresh:
            self.close()
            self.skipped_regions.clear()
            self._skipped_count = 0
            self._incomplete_reason = ""
        stamp = time.monotonic()
        self.progress = ScanProgress(phase=phase, started_at=stamp, last_checkpoint_at=stamp)
        self._reserved_bytes = 0

    def _done(self):
        if self._cancel.is_set():
            self._incomplete_reason = "cancelled"
        self.progress.phase = "cancelled" if self._cancel.is_set() else "partial" if self.skipped_regions or self._incomplete_reason else "complete"
        self.progress.matches = len(self.matches)

    def _skip(self, address, size, reason):
        self._skipped_count += 1
        if len(self.skipped_regions) < 4096:
            self.skipped_regions.append({"base": address, "size": size, "reason": str(reason)[:200]})
        self.progress.bytes_skipped += size
        self.progress.regions_skipped += 1

    def _read(self, address, size):
        if self._cancel.is_set():
            return b""
        self._check_limits()
        if self.max_read_bytes is not None and self.progress.bytes_read + size > self.max_read_bytes:
            self.progress.phase = "budget_exhausted"
            self._incomplete_reason = "budget_exhausted"
            raise ScanBudgetExhausted("scan read budget exhausted")
        try:
            raw = self.adapter.read_bytes(address, size)
        except (ScanCancelled, ScanBudgetExhausted):
            raise
        except Exception as exc:
            self._skip(address, size, exc)
            return b""
        if len(raw) > size:
            raise ValueError("adapter returned more bytes than requested")
        self.progress.bytes_read += len(raw)
        self._check_limits()
        if len(raw) != size:
            self._skip(address + len(raw), size - len(raw), "short_read")
        return raw

    def _iter_chunks(self, regions=None, *, overlap=0) -> Iterator[tuple[int, bytes, int]]:
        regions = self.adapter.regions_writable() if regions is None else regions
        for base, size in regions:
            if self._cancel.is_set():
                return
            if base <= 0 or size < 0 or base + size > 2**64:
                raise ValueError("invalid memory region")
            tail = b""
            chunk_size = min(_CHUNK_BYTES, self.chunk_bytes or getattr(self.adapter, "scan_chunk_bytes", _CHUNK_BYTES),
                             self.max_read_bytes or _CHUNK_BYTES)
            for offset in range(0, size, chunk_size):
                if self._cancel.is_set():
                    return
                take = min(chunk_size, size - offset)
                raw = self._read(base + offset, take)
                chunk = tail + raw
                if chunk:
                    yield base, chunk, base + offset - len(tail)
                tail = chunk[-overlap:] if overlap and len(raw) == take else b""
            self.progress.regions_covered += 1

    def _store_match(self, address, value):
        if self.max_matches is not None and len(self.matches) >= self.max_matches:
            self.progress.phase = "budget_exhausted"
            self._incomplete_reason = "budget_exhausted"
            raise ScanBudgetExhausted("scan match budget exhausted")
        # A slotted match + int + scalar + list slot is <160 bytes on CPython.
        # Reserve two chunks for live/snapshot comparisons. Byte values include payload size.
        bound = 192 + (len(value) if isinstance(value, bytes) else 0)
        if self._reserved_bytes + (len(self.matches) + 1) * bound + 3 * _CHUNK_BYTES > _RESIDENT_BUDGET_BYTES:
            self.progress.phase = "budget_exhausted"
            self._incomplete_reason = "budget_exhausted"
            raise ScanBudgetExhausted("resident match budget exhausted")
        self.matches.append(ScanMatch(address, value, self.value_type))
        self.progress.matches = len(self.matches)

    def _tick(self, callback=None):
        self._check_limits()
        if callback:
            callback(self.progress)
        if time.monotonic() - self.progress.last_checkpoint_at >= _CHECKPOINT_SECONDS:
            self._ensure_snapshot_dir()
            data = json.dumps({"session_id": self.session_id, "progress": self.progress.as_dict(),
                               "skipped_regions": self.skipped_regions, "snapshot_chunks": len(self._snapshots)})
            if self._snapshot_bytes + len(data.encode()) > _SNAPSHOT_BUDGET_BYTES:
                self.progress.phase = "budget_exhausted"
                raise ScanBudgetExhausted("checkpoint exceeds snapshot budget")
            temporary = self._snapshot_dir / "checkpoint.tmp"
            temporary.write_text(data)
            temporary.replace(self._snapshot_dir / "checkpoint.json")
            self.progress.last_checkpoint_at = time.monotonic()

    def _ensure_snapshot_dir(self):
        if self._snapshot_dir is None:
            self._snapshot_dir = Path(tempfile.mkdtemp(prefix="bughunter-scan-"))

    def _encode_value(self, value):
        fmt, size = _SCALAR_FMT[self.value_type]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("scan values must be finite")
        return struct.pack(fmt, value), size

    def export_checkpoint(self, directory):
        """Persist candidates and unknown baselines for refinement after helper restart.

        This resumes a scan *session*, not an interrupted region sweep. Coverage
        and an incomplete phase are retained rather than claiming missing bytes.
        """
        identity = getattr(self.adapter, "identity", None)
        if identity is None or not hasattr(identity, "to_wire"):
            raise ValueError("checkpoint requires process birth identity")
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            snapshot_rows = []
            for index, (address, size, path) in enumerate(self._snapshots):
                name = f"snapshot-{index:08x}.bin"
                shutil.copyfile(path, destination / name)
                snapshot_rows.append({"address": hex(address), "size": size, "file": name,
                                      "sha256": _file_digest(path)})
            matches_path = destination / "matches.jsonl"
            with matches_path.open("w", encoding="utf-8") as output:
                for match in self.matches:
                    value = match.value.hex() if self.value_type == "bytes" else match.value
                    output.write(json.dumps({"address": hex(match.address), "value": value}, allow_nan=False) + "\n")
            manifest = {"version": 1, "identity": identity.to_wire(), "session_id": self.session_id,
                        "value_type": self.value_type, "pattern_size": self._pattern_size,
                        "unknown": self._unknown, "snapshot_bytes": self._snapshot_bytes,
                        "incomplete_reason": self._incomplete_reason,
                        "skipped_count": self._skipped_count,
                        "snapshots": snapshot_rows, "match_count": len(self.matches),
                        "matches_sha256": _file_digest(matches_path),
                        "progress": self.progress.as_dict(), "skipped_regions": self.skipped_regions}
            (destination / "checkpoint.json").write_text(json.dumps(manifest, allow_nan=False), encoding="utf-8")
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return destination / "checkpoint.json"

    @classmethod
    def restore_checkpoint(cls, adapter, directory):
        """Restore a bounded checkpoint only into the identical process birth/build."""
        source = Path(directory)
        manifest_path = source / "checkpoint.json"
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("checkpoint manifest exceeds budget")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = getattr(adapter, "identity", None)
        if manifest.get("version") != 1 or identity is None or identity.to_wire() != manifest.get("identity"):
            raise ValueError("checkpoint process identity mismatch")
        value_type = manifest["value_type"]
        if value_type not in {*_SCALAR_FMT, "bytes"}:
            raise ValueError("invalid checkpoint value type")
        restored = cls(adapter, value_type, session_id=manifest["session_id"])
        try:
            restored._pattern_size = int(manifest["pattern_size"])
            if value_type == "bytes" and not 1 <= restored._pattern_size <= 4096:
                raise ValueError("invalid checkpoint byte pattern size")
            restored._unknown = bool(manifest["unknown"])
            restored._incomplete_reason = str(manifest.get("incomplete_reason", ""))
            restored._ensure_snapshot_dir()
            for index, row in enumerate(manifest["snapshots"]):
                name = row["file"]
                if name != f"snapshot-{index:08x}.bin":
                    raise ValueError("invalid checkpoint snapshot name")
                path = source / name
                size, address = int(row["size"]), int(row["address"], 16)
                if not 0 < size <= _CHUNK_BYTES + 4095 or not 0 < address < 2**64 - size:
                    raise ValueError("invalid checkpoint snapshot range")
                if restored._snapshot_bytes + size > _SNAPSHOT_BUDGET_BYTES or path.stat().st_size != size:
                    raise ValueError("checkpoint snapshot budget or length mismatch")
                if _file_digest(path) != row["sha256"]:
                    raise ValueError("checkpoint snapshot digest mismatch")
                copied = restored._snapshot_dir / name
                shutil.copyfile(path, copied)
                restored._snapshots.append((address, size, copied))
                restored._snapshot_bytes += size
            match_path = source / "matches.jsonl"
            if match_path.stat().st_size > _RESIDENT_BUDGET_BYTES:
                raise ValueError("checkpoint matches exceed budget")
            if _file_digest(match_path) != manifest["matches_sha256"]:
                raise ValueError("checkpoint matches digest mismatch")
            with match_path.open(encoding="utf-8") as input_file:
                for line in input_file:
                    row = json.loads(line)
                    address = int(row["address"], 16)
                    if not 0 < address < 2**64:
                        raise ValueError("invalid checkpoint match address")
                    value = bytes.fromhex(row["value"]) if value_type == "bytes" else row["value"]
                    if value_type != "bytes":
                        restored._encode_value(value)
                    elif len(value) != restored._pattern_size:
                        raise ValueError("checkpoint byte match size mismatch")
                    restored._store_match(address, value)
            if len(restored.matches) != manifest["match_count"] or restored._snapshot_bytes != manifest["snapshot_bytes"]:
                raise ValueError("checkpoint count mismatch")
            restored.progress = ScanProgress(**manifest["progress"])
            restored.skipped_regions = list(manifest["skipped_regions"])
            restored._skipped_count = int(manifest.get("skipped_count", len(restored.skipped_regions)))
            return restored
        except Exception:
            restored.close()
            raise

    def scan_exact(self, needle, *, regions=None, progress=None, byte_pattern=None):
        if byte_pattern is not None:
            if self.value_type != "bytes" or not isinstance(byte_pattern, (bytes, bytearray)) or not 1 <= len(byte_pattern) <= 4096:
                raise ValueError("byte_pattern requires value_type='bytes' and 1..4096 bytes")
            encoded = bytes(byte_pattern)
        else:
            encoded, _ = self._encode_value(needle)
        size = len(encoded)
        self._begin("scan", fresh=True)
        self._pattern_size = size
        value = encoded if self.value_type == "bytes" else struct.unpack(_SCALAR_FMT[self.value_type][0], encoded)[0]
        for _, chunk, address in self._iter_chunks(regions, overlap=size - 1):
            start = 0
            while not self._cancel.is_set():
                hit = chunk.find(encoded, start)
                if hit < 0:
                    break
                self._store_match(address + hit, value)
                start = hit + 1
                if len(self.matches) % 4096 == 0:
                    self._tick(progress)
            self._tick(progress)
        self._done()
        return self.matches

    def scan_range(self, low, high, *, regions=None, progress=None):
        self._encode_value(low); self._encode_value(high)
        if low > high:
            raise ValueError("range low exceeds high")
        fmt, size = _SCALAR_FMT[self.value_type]
        self._begin("scan", fresh=True)
        for _, chunk, address in self._iter_chunks(regions, overlap=size - 1):
            for offset in range(len(chunk) - size + 1):
                if offset % 4096 == 0:
                    self._tick(progress)
                    if self._cancel.is_set():
                        break
                value = struct.unpack_from(fmt, chunk, offset)[0]
                if low <= value <= high:
                    self._store_match(address + offset, value)
            self._tick(progress)
        self._done()
        return self.matches

    def scan_unknown_initial(self, *, regions=None, progress=None):
        _, size = _SCALAR_FMT[self.value_type]
        self._begin("scan", fresh=True)
        self._unknown = True
        self._ensure_snapshot_dir()
        for _, chunk, address in self._iter_chunks(regions, overlap=size - 1):
            if self._snapshot_bytes + len(chunk) > _SNAPSHOT_BUDGET_BYTES:
                self.progress.phase = "budget_exhausted"
                self._incomplete_reason = "budget_exhausted"
                raise ScanBudgetExhausted("snapshot budget exhausted")
            path = self._snapshot_dir / f"{len(self._snapshots):08x}.bin"
            path.write_bytes(chunk)
            self._snapshot_bytes += len(chunk)
            self._snapshots.append((address, len(chunk), path))
            self._tick(progress)
        self._done()
        return self._snapshot_bytes

    def refine(self, kind, *, needle=None, progress=None):
        if kind not in {"changed", "unchanged", "increased", "decreased", "exact"}:
            raise ValueError("unsupported refinement kind")
        if kind == "exact" and needle is None:
            raise ValueError("exact refinement requires a needle")
        if self.value_type == "bytes":
            size = self._pattern_size
            if kind not in {"exact", "changed", "unchanged"}:
                raise ValueError("byte refinement supports exact/changed/unchanged")
            if kind == "exact" and (not isinstance(needle, bytes) or len(needle) != size):
                raise ValueError("exact byte refinement must preserve pattern length")
            decode = lambda raw: raw
        else:
            fmt, size = _SCALAR_FMT[self.value_type]
            decode = lambda raw: struct.unpack(fmt, raw)[0]
            if kind == "exact":
                needle = decode(self._encode_value(needle)[0])
        self._begin("refine")
        previous, self.matches = self.matches, []
        self._reserved_bytes = sum(192 + (len(m.value) if isinstance(m.value, bytes) else 0) for m in previous)
        def pairs():
            if self._unknown:
                for address, length, path in self._snapshots:
                    if self._cancel.is_set():
                        return
                    old = path.read_bytes()
                    # Overlap may add size-1 bytes; keep each native read <=4 MiB.
                    chunk_size = min(_CHUNK_BYTES, self.chunk_bytes or _CHUNK_BYTES)
                    new = b"".join(self._read(address+i, min(chunk_size, length-i))
                                   for i in range(0, length, chunk_size))
                    if len(new) != length:
                        continue
                    for i in range(length - size + 1):
                        yield address+i, decode(old[i:i+size]), decode(new[i:i+size])
            else:
                # Group adjacent candidates into bounded reads rather than one RPC per byte.
                i = 0
                while i < len(previous):
                    if self._cancel.is_set():
                        return
                    start = i
                    base = previous[i].address
                    chunk_size = max(size, min(_CHUNK_BYTES, self.chunk_bytes or _CHUNK_BYTES))
                    while i < len(previous) and previous[i].address + size - base <= chunk_size:
                        i += 1
                    end = previous[i-1].address + size
                    raw = self._read(base, end-base)
                    for match in previous[start:i]:
                        off = match.address-base
                        if off+size <= len(raw):
                            yield match.address, match.value, decode(raw[off:off+size])
        for i, (address, old, value) in enumerate(pairs()):
            if i % 4096 == 0:
                self._tick(progress)
                if self._cancel.is_set():
                    break
            keep = (kind == "exact" and value == needle or kind == "changed" and value != old or
                    kind == "unchanged" and value == old or kind == "increased" and value > old or
                    kind == "decreased" and value < old)
            if keep:
                self._store_match(address, value)
        self._unknown = False
        self._done()
        return self.matches


__all__ = ["ScanSession", "ScanMatch", "ScanProgress", "ScanCancelled", "ScanBudgetExhausted"]
