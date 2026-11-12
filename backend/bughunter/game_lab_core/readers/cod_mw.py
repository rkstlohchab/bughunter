"""COD MW/WZ reader — custom Infinity Ward engine.

The engine pre-walks players into a structured table. Uses a raw
walker (not Unreal, not Source). Shipping skeleton + pointer-chain
pattern; per-build offsets re-derived via DMA offset_scan.
"""
from typing import Optional

from ..frame import Entity, ViewMatrix, Viewport
from ..engines.base import EngineWalker, Offsets


class CodMwWalker(EngineWalker):
    GAME_ID = "cod_mw"
    ANTICHEAT = "ricochet"
    MATRIX_ORDER = "column"
    MODULE_NAME = "ModernWarfare.exe"

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME)
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=1920, height=1080, fov_x=103.0, fov_y=70.0)

    def read_view_matrix(self) -> ViewMatrix:
        base = self.resolve_base()
        return ViewMatrix(data=self.r.read_mat44(base + self.off.require("ViewMatrix")))

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        local_idx = self.r.read_i32(base + self.off.require("LocalIndex"))
        if local_idx < 0 or local_idx > 150:
            return None
        return self._read_slot(local_idx, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        count = self.r.read_i32(base + self.off.require("ClientCount"))
        count = max(0, min(count, 150))
        out = []
        for i in range(count):
            ent = self._read_slot(i, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def _read_slot(self, idx: int, *, is_local: bool) -> Optional[Entity]:
        try:
            base = self.resolve_base()
            table = base + self.off.require("ClientTable")
            stride = int(self.off.require("ClientStride"))
            slot = table + idx * stride
            team = self.r.read_i32(slot + self.off.require("Team"))
            hp = self.r.read_i32(slot + self.off.require("Health"))
            pos = self.r.read_vec3(slot + self.off.require("Position"))
            head = (pos[0], pos[1], pos[2] + 72.0)
            return Entity(id=idx, kind="player", team=team, hp=max(0, hp),
                          head_world=head, feet_world=pos,
                          center_world=(pos[0], pos[1], pos[2] + 36.0),
                          is_local=is_local)
        except Exception:
            return None
