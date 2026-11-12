"""Engine walker base interface + MemoryReader abstraction."""
from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..frame import Entity, Frame, ViewMatrix, Viewport


# ---------------------------------------------------------------------------


class MemoryReader(ABC):
    """Uniform memory-read interface. Backends: DMA (MemProcFS), external
    RPM (Windows OpenProcess), ADB-forward (mobile Zygisk socket)."""

    @abstractmethod
    def read(self, address: int, size: int) -> bytes: ...

    @abstractmethod
    def resolve_module(self, name: str) -> int:
        """Return module base VA for ``name``."""

    def read_u8(self, addr: int) -> int:   return self.read(addr, 1)[0]
    def read_u16(self, addr: int) -> int:  return struct.unpack("<H", self.read(addr, 2))[0]
    def read_u32(self, addr: int) -> int:  return struct.unpack("<I", self.read(addr, 4))[0]
    def read_u64(self, addr: int) -> int:  return struct.unpack("<Q", self.read(addr, 8))[0]
    def read_i32(self, addr: int) -> int:  return struct.unpack("<i", self.read(addr, 4))[0]
    def read_i64(self, addr: int) -> int:  return struct.unpack("<q", self.read(addr, 8))[0]
    def read_f32(self, addr: int) -> float: return struct.unpack("<f", self.read(addr, 4))[0]

    def read_vec3(self, addr: int) -> tuple[float, float, float]:
        return struct.unpack("<fff", self.read(addr, 12))

    def read_mat44(self, addr: int) -> list[float]:
        return list(struct.unpack("<16f", self.read(addr, 64)))

    def read_cstr(self, addr: int, max_len: int = 64) -> str:
        buf = self.read(addr, max_len)
        end = buf.find(b"\x00")
        if end >= 0: buf = buf[:end]
        return buf.decode("utf-8", errors="replace")

    def read_wstr(self, addr: int, max_len: int = 64) -> str:
        buf = self.read(addr, max_len * 2)
        out = []
        for i in range(0, len(buf), 2):
            if buf[i] == 0 and buf[i + 1] == 0:
                break
            out.append(chr(buf[i] | (buf[i + 1] << 8)))
        return "".join(out)


# ---------------------------------------------------------------------------


@dataclass
class Offsets:
    """A per-game offset table. Keys are feature names; values are either
    integer offsets or nested dicts for structured data (e.g. bone IDs)."""
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = 0) -> Any:
        parts = key.split(".")
        cursor: Any = self.data
        for p in parts:
            if isinstance(cursor, dict) and p in cursor:
                cursor = cursor[p]
            else:
                return default
        return cursor

    def require(self, key: str) -> Any:
        v = self.get(key, None)
        if v is None:
            raise KeyError(f"offset missing: {key}")
        return v


# ---------------------------------------------------------------------------


class EngineWalker(ABC):
    """Per-engine implementation: iterates the entity list, fills Entity
    fields, computes visibility (if the engine exposes it)."""

    GAME_ID: str = ""
    ANTICHEAT: str = ""
    MATRIX_ORDER: str = "row"       # row | column
    MODULE_NAME: str = ""

    def __init__(self, reader: MemoryReader, offsets: Offsets):
        self.r = reader
        self.off = offsets
        self._base = 0
        self._tick = 0

    @abstractmethod
    def resolve_base(self) -> int:
        """Resolve the game's main module base address."""

    @abstractmethod
    def read_view_matrix(self) -> ViewMatrix: ...

    @abstractmethod
    def read_viewport(self) -> Viewport: ...

    @abstractmethod
    def read_local_player(self) -> Optional[Entity]: ...

    @abstractmethod
    def iter_players(self) -> list[Entity]: ...

    def iter_loot(self) -> list[Entity]:
        """Optional — override in walkers that support loot ESP."""
        return []

    def build_frame(self) -> Frame:
        self._tick += 1
        viewport = self.read_viewport()
        view = self.read_view_matrix()
        local = self.read_local_player()
        local_team = local.team if local else 0
        local_pos = local.center_world if local else (0.0, 0.0, 0.0)
        players = [p for p in self.iter_players() if not p.is_local]
        # distance fill
        for p in players:
            dx = p.center_world[0] - local_pos[0]
            dy = p.center_world[1] - local_pos[1]
            dz = p.center_world[2] - local_pos[2]
            p.distance_m = (dx * dx + dy * dy + dz * dz) ** 0.5 / 100.0  # engine units → m varies
        loot = self.iter_loot()
        return Frame(
            tick=self._tick, game=self.GAME_ID, anticheat=self.ANTICHEAT,
            viewport=viewport, view=view,
            local_team=local_team, local_pos=local_pos,
            entities=players, loot=loot,
        )
