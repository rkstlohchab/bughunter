"""Apex Legends (Source engine with Respawn modifications) walker."""
from __future__ import annotations

from typing import Optional

from ..frame import Entity, ViewMatrix, Viewport
from .base import EngineWalker, Offsets


class ApexWalker(EngineWalker):
    GAME_ID = "apex"
    ANTICHEAT = "eac"
    MATRIX_ORDER = "column"
    MODULE_NAME = "r5apex.exe"

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME)
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=1920, height=1080, fov_x=110.0, fov_y=72.0)

    def read_view_matrix(self) -> ViewMatrix:
        base = self.resolve_base()
        view_off = self.off.require("dwViewMatrix")
        return ViewMatrix(data=self.r.read_mat44(base + view_off))

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        local_off = self.off.require("dwLocalPlayer")
        local = self.r.read_u64(base + local_off)
        if not local:
            return None
        return self._fill(local, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        ent_start = base + self.off.require("dwEntityList")
        out: list[Entity] = []
        for i in range(0, 100):
            ptr = self.r.read_u64(ent_start + i * 32)
            if not ptr:
                continue
            sig = self.r.read_u64(ptr + self.off.require("m_iSignifierName"))
            if sig != self.off.require("signifier_player"):
                continue
            ent = self._fill(ptr, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def iter_loot(self) -> list[Entity]:
        base = self.resolve_base()
        ent_start = base + self.off.require("dwEntityList")
        out: list[Entity] = []
        for i in range(0, 20000):
            ptr = self.r.read_u64(ent_start + i * 32)
            if not ptr:
                continue
            sig = self.r.read_u64(ptr + self.off.require("m_iSignifierName"))
            if sig != self.off.require("signifier_loot"):
                continue
            try:
                name = self.r.read_cstr(ptr + self.off.require("m_itemName"), 48)
                pos = self.r.read_vec3(ptr + self.off.require("m_vecAbsOrigin"))
                out.append(Entity(id=i, kind="loot", name=name,
                                   center_world=pos, feet_world=pos, head_world=pos))
            except Exception:
                continue
        return out

    def _fill(self, ptr: int, *, is_local: bool) -> Optional[Entity]:
        try:
            team = self.r.read_i32(ptr + self.off.require("m_iTeamNum"))
            hp = self.r.read_i32(ptr + self.off.require("m_iHealth"))
            shield = 0
            shield_off = self.off.get("m_iShields", 0)
            if shield_off:
                try: shield = self.r.read_i32(ptr + shield_off)
                except Exception: pass
            pos = self.r.read_vec3(ptr + self.off.require("m_vecAbsOrigin"))
            head_world = (pos[0], pos[1], pos[2] + 72.0)
            bones: list[tuple[float, float, float]] = []
            bone_array_off = self.off.get("m_BoneArray", 0)
            if bone_array_off:
                try:
                    bone_array = self.r.read_u64(ptr + bone_array_off)
                    if bone_array:
                        bone_ids = self.off.get("Bones", {})
                        for cidx in range(17):
                            bid = int(bone_ids.get(_APEX_BONE_NAMES[cidx], 0))
                            bones.append(self.r.read_vec3(bone_array + bid * 48 + 12))
                        if bones:
                            head_world = bones[0]
                except Exception:
                    pass
            ent = Entity(
                id=ptr & 0xFFFFFFFF, kind="player",
                team=team, hp=max(0, hp),
                head_world=head_world, feet_world=pos,
                center_world=(pos[0], pos[1], pos[2] + 36.0),
                bones_world=bones, is_local=is_local,
                extra={"shield": shield},
            )
            name_off = self.off.get("m_szPlayerName", 0)
            if name_off:
                try:
                    ent.name = self.r.read_cstr(ptr + name_off, 32)
                except Exception:
                    pass
            return ent
        except Exception:
            return None


_APEX_BONE_NAMES = {
    0: "head", 1: "neck", 2: "pelvis", 3: "spine_up", 4: "spine_lo",
    5: "shoulder_l", 6: "elbow_l", 7: "hand_l",
    8: "shoulder_r", 9: "elbow_r", 10: "hand_r",
    11: "hip_l", 12: "knee_l", 13: "foot_l",
    14: "hip_r", 15: "knee_r", 16: "foot_r",
}
