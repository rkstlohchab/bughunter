"""Resumable, bounded sweeps over process memory regions.

A sweep keeps its position between calls, so a probe that runs out of its
time or byte budget continues where it stopped on the next operation
instead of restarting. Budget and cancellation exceptions from the reader
propagate unchanged; an unreadable or short chunk is recorded as skipped and
never interpreted.
"""
from __future__ import annotations

import re
import struct
import sys
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..scans import ScanBudgetExhausted, ScanCancelled

CHUNK_BYTES = 256 * 1024
_IDENT = re.compile(rb"[A-Za-z_<][A-Za-z0-9_<>`.$|=\-]*")


def normalise_regions(rows: Iterable) -> list[tuple[int, int]]:
    out = []
    for row in rows:
        if isinstance(row, dict):
            base, size = row["base"], row["size"]
            base = int(base, 16) if isinstance(base, str) else int(base)
        else:
            base, size = row
        base, size = int(base), int(size)
        if 0 < base and 0 < size and base + size <= 2**64:
            out.append((base, size))
    out.sort()
    merged: list[tuple[int, int]] = []
    for base, size in out:
        if merged and base < merged[-1][0] + merged[-1][1]:
            prev_base, prev_size = merged[-1]
            merged[-1] = (prev_base, max(prev_base + prev_size, base + size) - prev_base)
        else:
            merged.append((base, size))
    return merged


def readable_regions(adapter) -> list[tuple[int, int]]:
    """All readable regions when the adapter can list them, else writable ones."""
    regions = getattr(adapter, "regions", None)
    if callable(regions):
        try:
            return normalise_regions(regions(writable_only=False))
        except TypeError:
            pass
    return normalise_regions(adapter.regions_writable())


def contains(ranges: list[tuple[int, int]], address: int, width: int = 1) -> bool:
    for base, size in ranges:
        if base <= address and address + width <= base + size:
            return True
    return False


def qwords(raw: bytes) -> array:
    """Little-endian unsigned 64-bit view of ``raw`` (truncated to 8-byte multiple)."""
    values = array("Q")
    values.frombytes(raw[: len(raw) - len(raw) % 8])
    if sys.byteorder != "little":
        values.byteswap()
    return values


@dataclass
class Sweep:
    """Position inside an ordered region list; advance() resumes from it."""

    regions: list[tuple[int, int]]
    region_index: int = 0
    offset: int = 0
    bytes_read: int = 0
    skipped: list[dict] = field(default_factory=list)
    skipped_total: int = 0

    @property
    def done(self) -> bool:
        return self.region_index >= len(self.regions)

    @property
    def total_bytes(self) -> int:
        return sum(size for _, size in self.regions)

    def covered_bytes(self) -> int:
        covered = sum(size for _, size in self.regions[: self.region_index])
        return covered + (self.offset if not self.done else 0)

    def progress(self) -> dict:
        total = self.total_bytes
        return {"bytes_read": self.bytes_read, "covered_bytes": self.covered_bytes(),
                "total_bytes": total, "regions": len(self.regions),
                "skipped_regions": self.skipped_total, "done": self.done}

    def _skip(self, address: int, size: int, reason: str) -> None:
        self.skipped_total += 1
        if len(self.skipped) < 200:
            self.skipped.append({"base": hex(address), "size": size, "reason": reason[:160]})

    def advance(self, reader, visit: Callable[[bytes, int], None], *,
                chunk: int = CHUNK_BYTES, overlap: int = 0, stop: Callable[[], bool] | None = None) -> bool:
        """Visit chunks until the sweep ends, ``stop()`` is true, or the reader raises a budget error.

        ``visit(raw, address)`` receives full chunks only. Returns ``done``.
        """
        # A bounded reader advertises its per-call budget; never ask for more in one read,
        # otherwise a small budget fails every call before the sweep can move.
        limit = getattr(reader, "scan_chunk_bytes", None)
        if isinstance(limit, int) and limit > 0:
            chunk = max(overlap + 8, min(chunk, limit - limit % 8))
        if chunk % 8 or overlap % 8 or not 0 <= overlap < chunk:
            raise ValueError("chunk and overlap must be 8-byte multiples with overlap < chunk")
        while not self.done:
            base, size = self.regions[self.region_index]
            while self.offset < size:
                if stop is not None and stop():
                    return False
                take = min(chunk, size - self.offset)
                address = base + self.offset
                try:
                    raw = reader.read_bytes(address, take)
                except (ScanBudgetExhausted, ScanCancelled):
                    raise
                except Exception as exc:  # unreadable page: record, never interpret
                    self._skip(address, take, f"{type(exc).__name__}: {exc}")
                    raw = None
                if raw is not None:
                    if len(raw) != take:
                        self._skip(address, take, "short_read")
                    else:
                        self.bytes_read += take
                        visit(raw, address)
                at_end = self.offset + take >= size
                self.offset = size if at_end else self.offset + take - overlap
            self.region_index += 1
            self.offset = 0
        return True


def safe_read(reader, address: int, size: int) -> bytes | None:
    """Full read or None; budget/cancel errors propagate so a probe can resume."""
    try:
        raw = reader.read_bytes(address, size)
    except (ScanBudgetExhausted, ScanCancelled):
        raise
    except Exception:
        return None
    return raw if len(raw) == size else None


def read_cstring(reader, address: int, limit: int = 128) -> bytes | None:
    """NUL-terminated bytes at ``address``; None when unreadable or unterminated."""
    try:
        raw = reader.read_bytes(address, limit)
    except (ScanBudgetExhausted, ScanCancelled):
        raise
    except Exception:
        return None
    end = raw.find(b"\0")
    return raw[:end] if end >= 0 else None


def is_identifier(value: bytes | None, *, allow_empty: bool = False) -> bool:
    if value is None:
        return False
    if value == b"":
        return allow_empty
    return bool(_IDENT.fullmatch(value))


def read_u64(reader, address: int) -> int | None:
    try:
        raw = reader.read_bytes(address, 8)
    except (ScanBudgetExhausted, ScanCancelled):
        raise
    except Exception:
        return None
    return struct.unpack("<Q", raw)[0] if len(raw) == 8 else None


_ROOT_NAME = re.compile(r"[A-Za-z0-9._-]+")


def module_for(modules: list[tuple[str, int, int]], address: int) -> tuple[str, int] | None:
    for name, base, size in modules:
        if base <= address < base + size:
            return name, address - base
    return None


def root_for(modules: list[tuple[str, int, int]], address: int) -> str:
    """``mod:Name+0xRVA`` when the address is inside a module whose name the recipe grammar accepts, else ``abs:``."""
    hit = module_for(modules, address)
    if hit and _ROOT_NAME.fullmatch(hit[0]):
        return f"mod:{hit[0]}+0x{hit[1]:x}"
    return f"abs:0x{address:x}"


def module_ranges(adapter) -> list[tuple[str, int, int]]:
    rows = []
    getter = getattr(adapter, "modules", None)
    if not callable(getter):
        return rows
    for row in getter() or []:
        try:
            raw = row["base"]
            base = int(raw, 0) if isinstance(raw, str) else int(raw)
            size = int(row.get("size", 0))
            path = str(row.get("path") or row.get("name") or "")
        except (KeyError, TypeError, ValueError):
            continue
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if name and base > 0 and size > 0:
            rows.append((name, base, size))
    return rows
