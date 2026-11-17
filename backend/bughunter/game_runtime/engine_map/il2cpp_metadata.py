"""Static reader for IL2CPP ``global-metadata.dat``.

No per-version header struct is hardcoded. The header is accepted only when
it parses as a run of (offset, size[, count]) int32 records whose sections
tile the file: ascending, at most 3 bytes of alignment padding between
sections, the first section starting right after the header and the last
ending at the file size. Measured on Combat Master 0.30.4 (metadata version
39): 31 (offset, size, count) triples from byte 8 to byte 380 tiling all
17,519,268 bytes.

Section roles are not assumed. The identifier table is located by content:
the largest section that is almost entirely printable text split by NUL
bytes into identifier-like tokens. Mapping a name to its owning type needs
the type-definition record layout for the version, which is not sourced
here; the runtime probes in :mod:`.il2cpp_runtime` derive that instead.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0xFAB11BAF
_MAX_HEADER = 4096
_TOKEN = re.compile(rb"[A-Za-z_<][A-Za-z0-9_<>`.$|=\-]*")
_TEXT_BYTES = bytes([0, *range(32, 127)])


class MetadataError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Section:
    index: int
    offset: int
    size: int
    count: int | None


@dataclass
class Metadata:
    path: str
    version: int
    size: int
    header_bytes: int
    record_fields: int
    sections: list[Section]
    identifier_section: int | None
    _blob: bytes

    def summary(self) -> dict:
        ident = self.sections[self.identifier_section] if self.identifier_section is not None else None
        return {"path": self.path, "version": self.version, "file_size": self.size,
                "header_bytes": self.header_bytes,
                "header_layout": f"{len(self.sections)} x ({'offset,size,count' if self.record_fields == 3 else 'offset,size'}) int32 records tiling the file",
                "identifier_section": None if ident is None else
                {"index": ident.index, "offset": ident.offset, "size": ident.size, "count": ident.count}}

    def identifiers(self) -> list[str]:
        if self.identifier_section is None:
            return []
        section = self.sections[self.identifier_section]
        body = self._blob[section.offset:section.offset + section.size]
        return [token.decode("ascii") for token in body.split(b"\0") if _TOKEN.fullmatch(token)]


def _tiles(blob: bytes, fields: int) -> list[Section] | None:
    size = len(blob)
    first = struct.unpack_from("<i", blob, 8)[0]
    record = 4 * fields
    if not 8 + record <= first <= _MAX_HEADER or (first - 8) % record:
        return None
    sections = []
    cursor = first
    for index in range((first - 8) // record):
        values = struct.unpack_from("<" + "i" * fields, blob, 8 + index * record)
        offset, length = values[0], values[1]
        count = values[2] if fields == 3 else None
        if length < 0 or offset < cursor or offset - cursor > 3 or offset + length > size:
            return None
        if count is not None and count < 0:
            return None
        sections.append(Section(index, offset, length, count))
        cursor = offset + length
    if cursor != size:
        return None
    return sections


def _identifier_section(blob: bytes, sections: list[Section]) -> int | None:
    best, best_tokens = None, 0
    for section in sections:
        if section.size < 64:
            continue
        body = blob[section.offset:section.offset + section.size]
        other = len(body.translate(None, _TEXT_BYTES))
        if other / len(body) > 0.01:
            continue
        tokens = [t for t in body.split(b"\0") if t]
        good = sum(1 for t in tokens if _TOKEN.fullmatch(t))
        if tokens and good / len(tokens) >= 0.9 and good > best_tokens:
            best, best_tokens = section.index, good
    return best


def read_metadata(path: str | Path) -> Metadata:
    path = Path(path)
    blob = path.read_bytes()
    if len(blob) < 16:
        raise MetadataError("truncated", "metadata file is shorter than its header")
    magic, version = struct.unpack_from("<Ii", blob, 0)
    if magic != MAGIC:
        raise MetadataError("bad_magic", f"magic 0x{magic:08X} is not IL2CPP metadata (encrypted or not metadata)")
    for fields in (3, 2):
        sections = _tiles(blob, fields)
        if sections is not None:
            return Metadata(str(path), version, len(blob), sections[0].offset if sections else 8, fields,
                            sections, _identifier_section(blob, sections), blob)
    raise MetadataError("unsupported_header_layout",
                        f"version {version} header does not tile the file as int32 (offset,size[,count]) records; "
                        "inspect the first 0x200 bytes and add the measured layout here")


def search_identifiers(metadata: Metadata, query: str, *, limit: int = 100) -> dict:
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        raise ValueError("query must be 1..200 characters")
    limit = max(1, min(int(limit), 500))
    needle = query.lower()
    names = metadata.identifiers()
    seen, matches = set(), []
    for name in names:
        if needle in name.lower() and name not in seen:
            seen.add(name)
            matches.append(name)
    exact = [n for n in matches if n.lower() == needle]
    ordered = exact + sorted((n for n in matches if n not in exact), key=lambda n: (len(n), n))
    return {"query": query, "total": len(matches), "results": ordered[:limit],
            "truncated": len(matches) > limit, "identifier_count": len(names)}
