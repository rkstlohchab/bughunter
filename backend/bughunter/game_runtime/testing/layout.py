"""Shared layout spec for the disposable fixture target.

The layout is deterministic so the oracle, the restart harness and the
tests can reason about field offsets without ever asking the fixture for
them. Regions are referred to by name (``region_0``..``region_N-1``);
field offsets are expressed relative to the named region's current base.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


REGION_SIZE: int = 64 * 1024  # 64 KiB, the task specifies this.
DEFAULT_REGION_COUNT: int = 4


FieldType = Literal["u8", "u32", "f32", "u64", "pointer", "u32_array"]


@dataclass(frozen=True)
class FieldSpec:
    """A single field inside a named region."""

    name: str
    region: str
    offset: int
    type: FieldType
    # Optional literal initial value. Pointers are computed at boot from
    # ``pointer_to_region`` + ``pointer_to_offset`` because the target
    # addresses are not known until mmap returns them.
    initial: object = None
    pointer_to_region: str | None = None
    pointer_to_offset: int | None = None
    # Width in bytes for simple scalar fields.
    width: int = 0
    # For ``u32_array``: how many entries.
    length: int = 0


@dataclass(frozen=True)
class Layout:
    name: str
    region_count: int
    region_size: int
    fields: tuple[FieldSpec, ...]

    def field(self, name: str) -> FieldSpec:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(f"unknown field: {name!r}")


# Enemy array: 4 entries of {u32 id, u32 hp} starting at region_1 + 0x204.
_ENEMY_ENTRY_SIZE = 8
_ENEMY_COUNT = 4


DEFAULT_LAYOUT: Layout = Layout(
    name="DEFAULT",
    region_count=DEFAULT_REGION_COUNT,
    region_size=REGION_SIZE,
    fields=(
        FieldSpec(name="alive_flag", region="region_0", offset=0x10, type="u8", initial=1, width=1),
        FieldSpec(name="countdown", region="region_0", offset=0x20, type="u32", initial=60, width=4),
        FieldSpec(name="player_x", region="region_0", offset=0x40, type="f32", initial=0.0, width=4),
        FieldSpec(name="player_y", region="region_0", offset=0x44, type="f32", initial=0.0, width=4),
        FieldSpec(
            name="pointer",
            region="region_0",
            offset=0x100,
            type="pointer",
            pointer_to_region="region_1",
            pointer_to_offset=0x200,
            width=8,
        ),
        FieldSpec(name="enemy_count", region="region_1", offset=0x200, type="u32", initial=_ENEMY_COUNT, width=4),
        FieldSpec(
            name="enemy_entries",
            region="region_1",
            offset=0x204,
            type="u32_array",
            length=_ENEMY_COUNT * 2,  # id + hp interleaved
            width=_ENEMY_COUNT * _ENEMY_ENTRY_SIZE,
        ),
    ),
)


__all__ = [
    "REGION_SIZE",
    "DEFAULT_REGION_COUNT",
    "FieldSpec",
    "Layout",
    "DEFAULT_LAYOUT",
]
