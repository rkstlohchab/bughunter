"""Rainbow Six Siege reader — AnvilNext engine (Ubisoft). BattlEye."""
from ..frame import Entity, ViewMatrix, Viewport
from ..engines.base import EngineWalker, Offsets
from typing import Optional


class R6SiegeWalker(EngineWalker):
    GAME_ID = "r6_siege"
    ANTICHEAT = "battleye"
    MATRIX_ORDER = "column"
    MODULE_NAME = "RainbowSix.exe"

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME)
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=1920, height=1080, fov_x=84.0, fov_y=55.0)

    def read_view_matrix(self) -> ViewMatrix:
        base = self.resolve_base()
        off = self.off.require("ViewMatrix")
        return ViewMatrix(data=self.r.read_mat44(base + off))

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        local = self.r.read_u64(base + self.off.require("LocalPlayer"))
        if not local:
            return None
        return self._fill(local, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        list_ptr = self.r.read_u64(base + self.off.require("EntityList"))
        if not list_ptr:
            return []
        count = self.r.read_i32(list_ptr + self.off.require("Count"))
        items = self.r.read_u64(list_ptr + self.off.require("Items"))
        out: list[Entity] = []
        for i in range(max(0, min(count, 40))):
            ptr = self.r.read_u64(items + i * 8)
            if not ptr: continue
            ent = self._fill(ptr, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def _fill(self, ptr: int, *, is_local: bool) -> Optional[Entity]:
        try:
            team = self.r.read_i32(ptr + self.off.require("Team"))
            hp = self.r.read_i32(ptr + self.off.require("Health"))
            pos = self.r.read_vec3(ptr + self.off.require("Position"))
            head = (pos[0], pos[1], pos[2] + 1.7)
            bones: list[tuple[float, float, float]] = []
            bone_array_off = self.off.get("BoneArray", 0)
            if bone_array_off:
                try:
                    bone_array = self.r.read_u64(ptr + bone_array_off)
                    if bone_array:
                        ids = self.off.get("Bones", {})
                        stride = int(self.off.get("BoneStride", 48))
                        for cidx in range(17):
                            bid = int(ids.get(_R6_BONE_NAMES[cidx], 0))
                            bones.append(self.r.read_vec3(bone_array + bid * stride))
                        if bones:
                            head = bones[0]
                except Exception:
                    pass
            return Entity(id=ptr & 0xFFFFFFFF, kind="player",
                           team=team, hp=max(0, hp),
                           head_world=head, feet_world=pos,
                           center_world=(pos[0], pos[1], pos[2] + 0.85),
                           bones_world=bones, is_local=is_local)
        except Exception:
            return None


_R6_BONE_NAMES = {
    0: "head", 1: "neck", 2: "pelvis", 3: "spine_up", 4: "spine_lo",
    5: "shoulder_l", 6: "elbow_l", 7: "hand_l",
    8: "shoulder_r", 9: "elbow_r", 10: "hand_r",
    11: "hip_l", 12: "knee_l", 13: "foot_l",
    14: "hip_r", 15: "knee_r", 16: "foot_r",
}
