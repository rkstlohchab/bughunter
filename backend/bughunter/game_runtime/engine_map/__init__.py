"""Engine-aware memory mapping by measurement.

Nothing in this package carries an engine structure offset. Layouts are
derived from the live process (pointer references, self-references,
decodable names) or from files on disk, and every derived value is returned
with the evidence that produced it so callers can record it as a finding.
"""
from __future__ import annotations

from .detect import detect_engine
from .il2cpp_metadata import MetadataError, read_metadata, search_identifiers

__all__ = ["MetadataError", "detect_engine", "read_metadata", "search_identifiers"]
