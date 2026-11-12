"""Unreal Engine 4/5 walker — Valorant, Fortnite, Rainbow Six, PUBG PC,
Delta Force, and dozens of other UE titles.

Entity flow: GWorld → PersistentLevel → ActorArray[] → APawn.
Bones live in USkeletalMeshComponent -> bone transform array.
View matrix sits in APlayerController -> APlayerCameraManager.

Per-game offset table plugs into this walker; the flow is identical.
"""
from __future__ import annotations

import math
from typing import Optional

from ..frame import Entity, ViewMatrix, Viewport
from .base import EngineWalker, Offsets


class UnrealWalker(EngineWalker):
    GAME_ID = "unreal_generic"
    ANTICHEAT = "vanguard"
    MATRIX_ORDER = "row"
    MODULE_NAME = ""

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(self.MODULE_NAME or self._guess_module())
        return self._base

    def _guess_module(self) -> str:
        return {
            "valorant": "VALORANT-Win64-Shipping.exe",
            "fortnite": "FortniteClient-Win64-Shipping.exe",
            "r6_siege": "RainbowSix.exe",
            "pubg":     "TslGame.exe",
        }.get(self.GAME_ID, self.MODULE_NAME)

    def read_viewport(self) -> Viewport:
        # UE typically exposes FViewportClient->ViewportSize, but it's
        # unreliable mid-match. Return config default + let reader loop
        # override from monitor spec.
        return Viewport(width=1920, height=1080, fov_x=103.0, fov_y=71.0)

    def read_view_matrix(self) -> ViewMatrix:
        base = self.resolve_base()
        off_gworld = self.off.require("GWorld")
        gworld = self.r.read_u64(base + off_gworld)
        if not gworld:
            return ViewMatrix()
        owning_controller = self.r.read_u64(gworld + self.off.require("OwningGameInstance"))
        if not owning_controller:
            return ViewMatrix()
        local_players = self.r.read_u64(owning_controller + self.off.require("LocalPlayers"))
        local_player = self.r.read_u64(local_players) if local_players else 0
        if not local_player:
            return ViewMatrix()
        pc = self.r.read_u64(local_player + self.off.require("PlayerController"))
        if not pc:
            return ViewMatrix()
        cam_mgr = self.r.read_u64(pc + self.off.require("PlayerCameraManager"))
        if not cam_mgr:
            return ViewMatrix()
        # Camera POV is at PlayerCameraManager->CameraCache.POV
        # POV = FMinimalViewInfo { FVector Location, FRotator Rotation, float FOV, ... }
        pov_off = self.off.require("CameraCache.POV")
        cam_pos = self.r.read_vec3(cam_mgr + pov_off)                     # Location
        rot_off = pov_off + 12
        pitch = self.r.read_f32(cam_mgr + rot_off)
        yaw   = self.r.read_f32(cam_mgr + rot_off + 4)
        roll  = self.r.read_f32(cam_mgr + rot_off + 8)
        fov   = self.r.read_f32(cam_mgr + rot_off + 12)
        return self._build_view_matrix(cam_pos, pitch, yaw, roll, fov)

    def _build_view_matrix(self, cam_pos, pitch, yaw, roll, fov) -> ViewMatrix:
        """Compute view-proj matrix from camera pos + rotator. Row-major."""
        sp = math.sin(math.radians(pitch))
        cp = math.cos(math.radians(pitch))
        sy = math.sin(math.radians(yaw))
        cy = math.cos(math.radians(yaw))
        sr = math.sin(math.radians(roll))
        cr = math.cos(math.radians(roll))

        # Forward / right / up vectors (UE: X forward, Y right, Z up)
        fwd = (cp * cy, cp * sy, sp)
        right = (cr * sy - sr * sp * cy, -(cr * cy + sr * sp * sy), -sr * cp)
        up = (-(sr * sy + cr * sp * cy), (sr * cy - cr * sp * sy), -cr * cp)

        # Build column vectors of view matrix (world → camera)
        tx = -(right[0] * cam_pos[0] + right[1] * cam_pos[1] + right[2] * cam_pos[2])
        ty = -(up[0] * cam_pos[0] + up[1] * cam_pos[1] + up[2] * cam_pos[2])
        tz = -(fwd[0] * cam_pos[0] + fwd[1] * cam_pos[1] + fwd[2] * cam_pos[2])

        # Projection parameters
        aspect = 16 / 9
        fov_rad = math.radians(fov)
        f = 1.0 / math.tan(fov_rad / 2.0)
        znear = 10.0

        # Row-major combined view-projection (simplified — standard Unreal)
        m = [0.0] * 16
        m[0] = right[0] * f / aspect
        m[1] = up[0] * f
        m[2] = fwd[0]
        m[3] = fwd[0]
        m[4] = right[1] * f / aspect
        m[5] = up[1] * f
        m[6] = fwd[1]
        m[7] = fwd[1]
        m[8] = right[2] * f / aspect
        m[9] = up[2] * f
        m[10] = fwd[2]
        m[11] = fwd[2]
        m[12] = tx * f / aspect
        m[13] = ty * f
        m[14] = tz
        m[15] = tz
        return ViewMatrix(data=m)

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        gworld = self.r.read_u64(base + self.off.require("GWorld"))
        if not gworld:
            return None
        gi = self.r.read_u64(gworld + self.off.require("OwningGameInstance"))
        if not gi:
            return None
        lps = self.r.read_u64(gi + self.off.require("LocalPlayers"))
        if not lps:
            return None
        lp = self.r.read_u64(lps)
        if not lp:
            return None
        pc = self.r.read_u64(lp + self.off.require("PlayerController"))
        if not pc:
            return None
        pawn = self.r.read_u64(pc + self.off.require("AcknowledgedPawn"))
        if not pawn:
            return None
        return self._fill_pawn(pawn, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        gworld = self.r.read_u64(base + self.off.require("GWorld"))
        if not gworld:
            return []
        level = self.r.read_u64(gworld + self.off.require("PersistentLevel"))
        if not level:
            return []
        actors = self.r.read_u64(level + self.off.require("Actors"))
        count  = self.r.read_i32(level + self.off.require("ActorsNum"))
        count = max(0, min(count, 2000))
        out: list[Entity] = []
        for i in range(count):
            try:
                actor = self.r.read_u64(actors + i * 8)
            except Exception:
                continue
            if not actor:
                continue
            kind = self._classify(actor)
            if kind != "pawn":
                continue
            ent = self._fill_pawn(actor, is_local=False)
            if ent and ent.hp > 0:
                out.append(ent)
        return out

    def _classify(self, actor: int) -> str:
        """Fast class-name check — override per game for speed."""
        try:
            name_hint_off = self.off.get("Actor.ClassName", None)
            if name_hint_off is None:
                return "pawn"
            cls = self.r.read_u64(actor + name_hint_off)
            if not cls:
                return "unknown"
            # cheap tag check — per-game walker override for real classification
            return "pawn"
        except Exception:
            return "unknown"

    def _fill_pawn(self, pawn: int, *, is_local: bool) -> Optional[Entity]:
        try:
            team = self.r.read_i32(pawn + self.off.get("TeamIndex", 0))
            hp = int(self.r.read_f32(pawn + self.off.require("Health")))
            root = self.r.read_u64(pawn + self.off.require("RootComponent"))
            if not root:
                return None
            pos = self.r.read_vec3(root + self.off.require("RelativeLocation"))
            head = (pos[0], pos[1], pos[2] + 85.0)
            feet = (pos[0], pos[1], pos[2])
            bones: list[tuple[float, float, float]] = []
            mesh = self.r.read_u64(pawn + self.off.get("Mesh", 0))
            if mesh:
                bone_array = self.r.read_u64(mesh + self.off.get("BoneArray", 0))
                if bone_array:
                    bone_ids = self.off.get("Bones", {})
                    for canonical_idx in range(17):
                        bid = int(bone_ids.get(_UE_BONE_NAMES[canonical_idx], 0))
                        try:
                            bones.append(self.r.read_vec3(bone_array + bid * 48 + 16))
                        except Exception:
                            bones.append(feet)
                    if bones:
                        head = bones[0]
            ent = Entity(
                id=pawn & 0xFFFFFFFF,
                kind="player",
                team=team, hp=max(0, hp),
                head_world=head,
                feet_world=feet,
                center_world=(pos[0], pos[1], pos[2] + 40.0),
                bones_world=bones,
                is_local=is_local,
            )
            name_off = self.off.get("PlayerName", 0)
            if name_off:
                try:
                    name_ptr = self.r.read_u64(pawn + name_off)
                    if name_ptr:
                        ent.name = self.r.read_wstr(name_ptr, 32)
                except Exception:
                    pass
            return ent
        except Exception:
            return None


_UE_BONE_NAMES = {
    0: "head", 1: "neck", 2: "pelvis", 3: "spine_up", 4: "spine_lo",
    5: "shoulder_l", 6: "elbow_l", 7: "hand_l",
    8: "shoulder_r", 9: "elbow_r", 10: "hand_r",
    11: "hip_l", 12: "knee_l", 13: "foot_l",
    14: "hip_r", 15: "knee_r", 16: "foot_r",
}
