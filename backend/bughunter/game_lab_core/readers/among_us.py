"""Among Us reader — Unity IL2CPP with memory writes for no-cooldown.

Universal Frame output + a side channel for the write-loop that
overrides ``PlayerControl.killTimer`` on LocalPlayer. The write loop
is driven by the ``among_us_cheat`` adapter, not the frame-emitter loop
— two parallel pipelines, same reader, same offset table.
"""
from __future__ import annotations

import struct
from typing import Optional

from ..frame import Entity, Frame, ViewMatrix, Viewport
from ..engines.base import EngineWalker, Offsets, MemoryReader


class AmongUsWalker(EngineWalker):
    GAME_ID = "among_us"
    ANTICHEAT = "eac-light"
    MATRIX_ORDER = "row"
    MODULE_NAME = "GameAssembly.dylib"   # overridden per platform at runtime

    def resolve_base(self) -> int:
        if not self._base:
            # Try Mac / iOS / Android variants
            for name in (self.MODULE_NAME,
                          "GameAssembly.dll",
                          "libil2cpp.so",
                          "AmongUs"):
                b = self.r.resolve_module(name)
                if b:
                    self._base = b
                    self.MODULE_NAME = name
                    break
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=1920, height=1080, fov_x=100.0, fov_y=56.0)

    def read_view_matrix(self) -> ViewMatrix:
        # Among Us is 2D orthographic — build a simple world→screen that
        # maps the (x, y) plane via the camera position + ortho size.
        base = self.resolve_base()
        cam_off = self.off.get("Camera.main", 0)
        if not cam_off:
            return ViewMatrix()
        cam = self.r.read_u64(base + cam_off)
        if not cam:
            return ViewMatrix()
        pos = self.r.read_vec3(cam + self.off.get("Camera.transform_pos", 0x38))
        ortho = self.r.read_f32(cam + self.off.get("Camera.orthographicSize", 0xF4))
        if ortho < 0.1:
            ortho = 3.0   # Among Us default
        # Build an ortho matrix (row-major) equivalent to the one the
        # universal overlay expects: screen_x = (world_x - cam_x) / ortho * aspect
        aspect = 16 / 9
        m = [0.0] * 16
        m[0]  = 1.0 / (ortho * aspect); m[3]  = -pos[0] / (ortho * aspect)
        m[5]  = 1.0 / ortho;             m[7]  = -pos[1] / ortho
        m[15] = 1.0
        # Clip-w stays 1.0 so world_to_screen's cw >= 0.01 check passes.
        m[13] = 0.0
        m[14] = 0.0
        return ViewMatrix(data=m)

    def read_local_player(self) -> Optional[Entity]:
        base = self.resolve_base()
        local_ptr_off = self.off.get("PlayerControl_LocalPlayer", 0)
        if not local_ptr_off:
            return None
        local = self.r.read_u64(base + local_ptr_off)
        if not local:
            return None
        return self._fill(local, is_local=True)

    def iter_players(self) -> list[Entity]:
        base = self.resolve_base()
        list_off = self.off.get("PlayerControl_AllPlayerControls", 0)
        if not list_off:
            return []
        list_ptr = self.r.read_u64(base + list_off)
        if not list_ptr:
            return []
        items = self.r.read_u64(list_ptr + 0x10)
        count = self.r.read_i32(list_ptr + 0x18)
        count = max(0, min(count, 15))   # 15-player max
        out: list[Entity] = []
        for i in range(count):
            try:
                pc = self.r.read_u64(items + 0x20 + i * 8)
            except Exception:
                continue
            if not pc:
                continue
            e = self._fill(pc, is_local=False)
            if e:
                out.append(e)
        return out

    def _fill(self, pc: int, *, is_local: bool) -> Optional[Entity]:
        try:
            data_ptr  = self.r.read_u64(pc + self.off.require("PlayerControl.Data"))
            phys_ptr  = self.r.read_u64(pc + self.off.require("PlayerControl.MyPhysics"))
            body_ptr  = self.r.read_u64(phys_ptr + self.off.require("PlayerPhysics.body")) if phys_ptr else 0
            # Rigidbody2D.position is at a known offset; Unity ships it as 2 floats.
            pos2d = (0.0, 0.0)
            if body_ptr:
                pos_off = self.off.get("Rigidbody2D.position", 0x34)
                buf = self.r.read(body_ptr + pos_off, 8)
                pos2d = struct.unpack("<ff", buf)
            is_imp  = bool(self.r.read_u8(data_ptr + self.off.require("PlayerInfo.IsImpostor"))) if data_ptr else False
            is_dead = bool(self.r.read_u8(data_ptr + self.off.require("PlayerInfo.IsDead"))) if data_ptr else False
            name = ""
            if data_ptr:
                np_ptr = self.r.read_u64(data_ptr + self.off.require("PlayerInfo.PlayerName"))
                if np_ptr:
                    name = self.r.read_cstr(np_ptr + 0x14, 32)
            vent = False
            vent_off = self.off.get("PlayerControl.inVent", 0)
            if vent_off:
                try: vent = bool(self.r.read_u8(pc + vent_off))
                except Exception: vent = False
            # In 2D world coords: use team 0 = crew, 1 = impostor, 2 = dead for
            # team-colored chams in the universal overlay.
            team = 1 if is_imp else 0
            if is_dead: team = 2
            pos = (pos2d[0], pos2d[1], 0.0)
            ent = Entity(
                id=pc & 0xFFFFFFFF, kind="player",
                team=team, hp=(0 if is_dead else 100),
                name=name,
                head_world=(pos[0], pos[1] + 0.4, 0.0),
                feet_world=(pos[0], pos[1] - 0.5, 0.0),
                center_world=pos,
                is_local=is_local,
                extra={"impostor": is_imp, "dead": is_dead, "vent": vent},
            )
            return ent
        except Exception:
            return None

    # ---- side-channel writers used by among_us_cheat adapter ------------

    def write_kill_cooldown_zero(self) -> bool:
        base = self.resolve_base()
        local = self.r.read_u64(base + self.off.require("PlayerControl_LocalPlayer"))
        if not local:
            return False
        off = self.off.require("PlayerControl.killTimer")
        # Write via the MemoryReader (requires a write-capable backend —
        # MemProcFS with DMA, or the Android/iOS dylib bridge).
        return self._write(local + off, struct.pack("<f", 0.0))

    def write_fog_off(self) -> bool:
        base = self.resolve_base()
        ss_off = self.off.get("ShipStatus_Instance", 0)
        if not ss_off:
            return False
        ss = self.r.read_u64(base + ss_off)
        if not ss:
            return False
        ls = self.r.read_u64(ss + self.off.require("ShipStatus.lightSystem"))
        if not ls:
            return False
        radius_off = self.off.require("LightSystem.PrimaryRadius")
        return self._write(ls + radius_off, struct.pack("<f", 10000.0))

    def _write(self, addr: int, buf: bytes) -> bool:
        write = getattr(self.r, "write", None)
        if write and callable(write):
            try:
                return bool(write(addr, buf))
            except Exception:
                return False
        # The Android/iOS bridge exposes write via a cmd=write JSON call.
        sock = getattr(self.r, "sock", None)
        if sock:
            import json
            try:
                sock.sendall((json.dumps({
                    "cmd": "write", "addr": addr,
                    "bytes": buf.hex(),
                }) + "\n").encode())
                # best-effort — we don't block on ack
                return True
            except Exception:
                return False
        return False
