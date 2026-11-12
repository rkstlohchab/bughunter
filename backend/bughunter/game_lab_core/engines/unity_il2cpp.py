"""Unity IL2CPP walker — Rust, Escape from Tarkov, Fall Guys, Among Us, +many.

Entity flow: GameAssembly.dll's static class list → per-class instances via
IL2CPP metadata. Trees are laid out differently per title; this walker
exposes the primitives (resolve class, iterate instances, read fields) and
leaves per-game routing to the reader module.
"""
from __future__ import annotations

from typing import Optional

from ..frame import Entity, ViewMatrix, Viewport
from .base import EngineWalker, Offsets


class UnityIl2CppWalker(EngineWalker):
    GAME_ID = "unity_il2cpp_generic"
    ANTICHEAT = "eac"
    MATRIX_ORDER = "row"
    MODULE_NAME = "GameAssembly.dll"
    UNITY_PLAYER = "UnityPlayer.dll"

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME)
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=1920, height=1080, fov_x=90.0, fov_y=59.0)

    def read_view_matrix(self) -> ViewMatrix:
        base = self.r.resolve_module(self.UNITY_PLAYER)
        off = self.off.require("CameraViewMatrix")
        return ViewMatrix(data=self.r.read_mat44(base + off))

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        off = self.off.require("LocalPlayer")
        ptr = self.r.read_u64(base + off)
        if not ptr:
            return None
        return self._fill(ptr, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        list_off = self.off.require("PlayerList")
        list_ptr = self.r.read_u64(base + list_off)
        if not list_ptr:
            return []
        items = self.r.read_u64(list_ptr + self.off.require("ListItems"))
        count = self.r.read_i32(list_ptr + self.off.require("ListCount"))
        count = max(0, min(count, 2000))
        out: list[Entity] = []
        for i in range(count):
            try:
                ptr = self.r.read_u64(items + 0x20 + i * 8)
            except Exception:
                continue
            if not ptr:
                continue
            ent = self._fill(ptr, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def iter_loot(self) -> list[Entity]:
        base = self.resolve_base()
        loot_root_off = self.off.get("LootRoot", 0)
        if not loot_root_off:
            return []
        root = self.r.read_u64(base + loot_root_off)
        if not root:
            return []
        items = self.r.read_u64(root + self.off.get("ListItems", 0x10))
        count = self.r.read_i32(root + self.off.get("ListCount", 0x18))
        count = max(0, min(count, 20000))
        out: list[Entity] = []
        for i in range(count):
            try:
                ptr = self.r.read_u64(items + 0x20 + i * 8)
            except Exception:
                continue
            if not ptr: continue
            try:
                pos = self.r.read_vec3(ptr + self.off.get("WorldPos", 0x20))
                name_ptr = self.r.read_u64(ptr + self.off.get("NameString", 0x30))
                name = self.r.read_cstr(name_ptr + 0x14, 48) if name_ptr else ""
                out.append(Entity(id=i, kind="loot", name=name,
                                   center_world=pos, feet_world=pos, head_world=pos))
            except Exception:
                continue
        return out

    def _fill(self, pawn: int, *, is_local: bool) -> Optional[Entity]:
        try:
            hp = int(self.r.read_f32(pawn + self.off.require("Health")))
            team = self.r.read_i32(pawn + self.off.get("TeamId", 0))
            transform = self.r.read_u64(pawn + self.off.require("Transform"))
            pos = self.r.read_vec3(transform + self.off.get("TransformPos", 0x90))
            head = (pos[0], pos[1] + 1.7, pos[2])
            bones: list[tuple[float, float, float]] = []
            bone_root_off = self.off.get("BoneRoot", 0)
            if bone_root_off:
                try:
                    bone_root = self.r.read_u64(pawn + bone_root_off)
                    if bone_root:
                        bone_ids = self.off.get("Bones", {})
                        bone_stride = int(self.off.get("BoneStride", 48))
                        for cidx in range(17):
                            bid = int(bone_ids.get(_UNITY_BONE_NAMES[cidx], 0))
                            bones.append(self.r.read_vec3(bone_root + bid * bone_stride))
                        if bones:
                            head = bones[0]
                except Exception:
                    pass
            ent = Entity(
                id=pawn & 0xFFFFFFFF, kind="player",
                team=team, hp=max(0, hp),
                head_world=head, feet_world=pos,
                center_world=(pos[0], pos[1] + 0.9, pos[2]),
                bones_world=bones, is_local=is_local,
            )
            name_off = self.off.get("DisplayName", 0)
            if name_off:
                try:
                    ptr = self.r.read_u64(pawn + name_off)
                    if ptr:
                        ent.name = self.r.read_cstr(ptr + 0x14, 32)
                except Exception:
                    pass
            return ent
        except Exception:
            return None


_UNITY_BONE_NAMES = {
    0: "head", 1: "neck", 2: "pelvis", 3: "spine_up", 4: "spine_lo",
    5: "shoulder_l", 6: "elbow_l", 7: "hand_l",
    8: "shoulder_r", 9: "elbow_r", 10: "hand_r",
    11: "hip_l", 12: "knee_l", 13: "foot_l",
    14: "hip_r", 15: "knee_r", 16: "foot_r",
}
