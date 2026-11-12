"""Source 2 engine walker — CS2, Deadlock, Dota 2.

Per-game offsets live in ``cheats/game_data/<game>.offsets.json`` and
auto-refresh from ``a2x/cs2-dumper`` for CS2.
"""
from __future__ import annotations

from typing import Optional

from ..frame import Entity, Frame, ViewMatrix, Viewport
from .base import EngineWalker, Offsets


class Source2Walker(EngineWalker):
    GAME_ID = "cs2"
    ANTICHEAT = "vac-trust"
    MATRIX_ORDER = "row"
    MODULE_NAME = "client.dll"

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME)
        return self._base

    def read_view_matrix(self) -> ViewMatrix:
        off = self.off.require("dwViewMatrix")
        return ViewMatrix(data=self.r.read_mat44(self.resolve_base() + off))

    def read_viewport(self) -> Viewport:
        # Source 2 stores viewport width/height in an engine-resource
        # module. For simplicity, screen is config-driven; the reader loop
        # overrides from the user's monitor spec. Default 1080p.
        return Viewport(width=1920, height=1080, fov_x=90.0, fov_y=59.0)

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        local_ctl_off = self.off.require("dwLocalPlayerController")
        pawn_off = self.off.require("m_hPlayerPawn")
        controller = self.r.read_u64(base + local_ctl_off)
        if not controller:
            return None
        pawn_handle = self.r.read_u32(controller + pawn_off)
        if not pawn_handle:
            return None
        pawn = self._resolve_handle(pawn_handle)
        if not pawn:
            return None
        return self._fill_entity(pawn, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        list_off = self.off.require("dwEntityList")
        entity_list = base + list_off
        out: list[Entity] = []
        for i in range(1, 64):
            list_entry = self.r.read_u64(entity_list + 8 * (i >> 9) + 16)
            if not list_entry:
                continue
            try:
                pawn = self.r.read_u64(list_entry + 120 * (i & 0x1FF))
            except Exception:
                continue
            if not pawn:
                continue
            ent = self._fill_entity(pawn, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def _resolve_handle(self, handle: int) -> int:
        """Translate an entity handle to a pointer."""
        base = self.resolve_base()
        list_off = self.off.require("dwEntityList")
        entity_list = base + list_off
        idx = handle & 0x7FFF
        list_entry = self.r.read_u64(entity_list + 8 * (idx >> 9) + 16)
        if not list_entry:
            return 0
        return self.r.read_u64(list_entry + 120 * (idx & 0x1FF))

    def _fill_entity(self, pawn: int, *, is_local: bool) -> Optional[Entity]:
        try:
            team = self.r.read_i32(pawn + self.off.require("m_iTeamNum"))
            hp = self.r.read_i32(pawn + self.off.require("m_iHealth"))
            pos = self.r.read_vec3(pawn + self.off.require("m_vOldOrigin"))
            scene = self.r.read_u64(pawn + self.off.require("m_pGameSceneNode"))
            bone_array_ptr = 0
            bones: list[tuple[float, float, float]] = []
            head_world = (pos[0], pos[1], pos[2] + 72.0)   # Source 2 default head Z offset
            if scene:
                bone_array_ptr = self.r.read_u64(scene + self.off.require("m_modelState_m_bonesArray"))
            if bone_array_ptr:
                bone_ids = self.off.get("bones", {})
                head_id = int(bone_ids.get("head", 6))
                head_world = self.r.read_vec3(bone_array_ptr + head_id * 32)
                for canonical_idx in range(17):
                    key = _CANONICAL_BONE_NAMES[canonical_idx]
                    bid = bone_ids.get(key, head_id)
                    try:
                        bones.append(self.r.read_vec3(bone_array_ptr + int(bid) * 32))
                    except Exception:
                        bones.append(head_world)
            ent = Entity(
                id=pawn & 0xFFFFFFFF,
                kind="player",
                team=team, hp=max(0, hp),
                head_world=head_world,
                feet_world=(pos[0], pos[1], pos[2]),
                center_world=(pos[0], pos[1], pos[2] + 36.0),
                bones_world=bones,
                is_local=is_local,
            )
            # name — CCSPlayerController -> m_sSanitizedPlayerName (char*)
            try:
                name_ptr = self.r.read_u64(pawn + self.off.get("m_sSanitizedPlayerName", 0))
                if name_ptr:
                    ent.name = self.r.read_cstr(name_ptr, 32)
            except Exception:
                pass
            return ent
        except Exception:
            return None


_CANONICAL_BONE_NAMES = {
    0: "head", 1: "neck", 2: "pelvis",
    3: "spine_up", 4: "spine_lo",
    5: "shoulder_l", 6: "elbow_l", 7: "hand_l",
    8: "shoulder_r", 9: "elbow_r", 10: "hand_r",
    11: "hip_l", 12: "knee_l", 13: "foot_l",
    14: "hip_r", 15: "knee_r", 16: "foot_r",
}
