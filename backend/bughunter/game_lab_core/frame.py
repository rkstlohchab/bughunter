"""Universal frame schema.

Every reader emits a ``Frame`` per tick. The overlay renderer consumes
``Frame`` regardless of game or engine. That's the whole point: one
drawing pipeline, 13 games, pluggable readers.

Bone indices map to a canonical skeleton (shoulders, elbows, hands,
hips, knees, feet, head, neck, pelvis). Each engine walker translates
its own bone IDs to the canonical set so ``skeleton`` draws the same
lines for every game.
"""
from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional


# Canonical bone IDs. Order matters for skeleton line drawing.
BONE_HEAD = 0
BONE_NECK = 1
BONE_PELVIS = 2
BONE_SPINE_UP = 3
BONE_SPINE_LO = 4
BONE_SHOULDER_L = 5
BONE_ELBOW_L = 6
BONE_HAND_L = 7
BONE_SHOULDER_R = 8
BONE_ELBOW_R = 9
BONE_HAND_R = 10
BONE_HIP_L = 11
BONE_KNEE_L = 12
BONE_FOOT_L = 13
BONE_HIP_R = 14
BONE_KNEE_R = 15
BONE_FOOT_R = 16
BONE_COUNT = 17

# Edges drawn as skeleton lines
SKELETON_EDGES = [
    (BONE_HEAD, BONE_NECK),
    (BONE_NECK, BONE_SHOULDER_L), (BONE_SHOULDER_L, BONE_ELBOW_L), (BONE_ELBOW_L, BONE_HAND_L),
    (BONE_NECK, BONE_SHOULDER_R), (BONE_SHOULDER_R, BONE_ELBOW_R), (BONE_ELBOW_R, BONE_HAND_R),
    (BONE_NECK, BONE_SPINE_UP), (BONE_SPINE_UP, BONE_SPINE_LO), (BONE_SPINE_LO, BONE_PELVIS),
    (BONE_PELVIS, BONE_HIP_L), (BONE_HIP_L, BONE_KNEE_L), (BONE_KNEE_L, BONE_FOOT_L),
    (BONE_PELVIS, BONE_HIP_R), (BONE_HIP_R, BONE_KNEE_R), (BONE_KNEE_R, BONE_FOOT_R),
]


# ---------------------------------------------------------------------------


@dataclass
class ViewMatrix:
    """Row-major 4×4 view-projection matrix. Serialized as a flat list."""
    data: list[float] = field(default_factory=lambda: [0.0] * 16)

    @classmethod
    def from_flat(cls, flat: list[float]) -> "ViewMatrix":
        return cls(data=list(flat))

    def row(self, i: int) -> list[float]:
        return self.data[i * 4 : i * 4 + 4]


@dataclass
class Viewport:
    width: int = 1920
    height: int = 1080
    fov_x: float = 90.0
    fov_y: float = 59.0


@dataclass
class Entity:
    """One player/NPC/loot item. All world coords are (x, y, z) in game units."""
    id: int = 0
    kind: str = "player"            # player | npc | loot | vehicle | corpse
    team: int = 0
    hp: int = 100
    max_hp: int = 100
    name: str = ""
    weapon: str = ""
    head_world: tuple[float, float, float] = (0.0, 0.0, 0.0)
    feet_world: tuple[float, float, float] = (0.0, 0.0, 0.0)
    center_world: tuple[float, float, float] = (0.0, 0.0, 0.0)
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bones_world: list[tuple[float, float, float]] = field(default_factory=list)
    visible: bool = False
    distance_m: float = 0.0
    is_local: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Frame:
    ts: float = field(default_factory=time.time)
    tick: int = 0
    game: str = ""
    anticheat: str = ""
    viewport: Viewport = field(default_factory=Viewport)
    view: ViewMatrix = field(default_factory=ViewMatrix)
    camera_world: tuple[float, float, float] = (0.0, 0.0, 0.0)
    camera_yaw: float = 0.0
    camera_pitch: float = 0.0
    local_team: int = 0
    local_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    entities: list[Entity] = field(default_factory=list)
    loot: list[Entity] = field(default_factory=list)
    status: str = "ok"              # ok | reader_error | stale
    reader_fps: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=_dict_default, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "Frame":
        d = json.loads(s)
        view = ViewMatrix(data=d.get("view", {}).get("data", [0.0] * 16))
        vp = Viewport(**d.get("viewport", {}))
        ents = [Entity(**e) for e in d.get("entities", [])]
        loot = [Entity(**e) for e in d.get("loot", [])]
        d.pop("view", None); d.pop("viewport", None)
        d.pop("entities", None); d.pop("loot", None)
        return cls(view=view, viewport=vp, entities=ents, loot=loot, **d)


def _dict_default(o):
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    return o


# ---------------------------------------------------------------------------


class FrameEmitter:
    """Push a ``Frame`` to one or more sinks.

    Sinks:
    - ``shared_file`` (/tmp/gamelab_frame.json by default) — atomic rewrite
    - ``udp_socket`` (127.0.0.1:65501) — low-latency same-host overlay
    - ``callback`` — in-process overlay
    """

    def __init__(self, *, shared_file: Optional[str] = "/tmp/gamelab_frame.json",
                 udp: Optional[tuple[str, int]] = ("127.0.0.1", 65501),
                 callback: Optional[Callable[[Frame], None]] = None):
        self.shared_file = Path(shared_file) if shared_file else None
        self.udp = udp
        self.callback = callback
        self._udp_sock: Optional[socket.socket] = None
        if udp:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def emit(self, frame: Frame) -> None:
        payload = frame.to_json()
        if self.shared_file:
            tmp = self.shared_file.with_suffix(self.shared_file.suffix + ".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.shared_file)
            except Exception:
                pass
        if self._udp_sock and self.udp:
            try:
                data = payload.encode("utf-8")
                # UDP max per packet ~65507; a crowded frame can exceed that.
                # Overlay reconstructs from shared_file on overflow.
                if len(data) < 65000:
                    self._udp_sock.sendto(data, self.udp)
            except Exception:
                pass
        if self.callback:
            try:
                self.callback(frame)
            except Exception:
                pass

    def close(self) -> None:
        if self._udp_sock:
            try: self._udp_sock.close()
            except Exception: pass


# ---------------------------------------------------------------------------


def world_to_screen(world: tuple[float, float, float], mat: ViewMatrix,
                     screen_w: int, screen_h: int) -> Optional[tuple[float, float]]:
    """Row-major 4×4 view-proj multiply, then perspective divide."""
    x, y, z = world
    m = mat.data
    cx = m[0] * x + m[1] * y + m[2] * z + m[3]
    cy = m[4] * x + m[5] * y + m[6] * z + m[7]
    cw = m[12] * x + m[13] * y + m[14] * z + m[15]
    if cw < 0.01:
        return None
    ndc_x = cx / cw
    ndc_y = cy / cw
    sx = (1.0 + ndc_x) * 0.5 * screen_w
    sy = (1.0 - ndc_y) * 0.5 * screen_h
    return (sx, sy)


def world_to_screen_column_major(world: tuple[float, float, float], mat: ViewMatrix,
                                   screen_w: int, screen_h: int) -> Optional[tuple[float, float]]:
    """Column-major variant (Source engine, Source 2, Apex)."""
    x, y, z = world
    m = mat.data
    cx = m[0] * x + m[4] * y + m[8] * z + m[12]
    cy = m[1] * x + m[5] * y + m[9] * z + m[13]
    cw = m[3] * x + m[7] * y + m[11] * z + m[15]
    if cw < 0.01:
        return None
    ndc_x = cx / cw
    ndc_y = cy / cw
    sx = (1.0 + ndc_x) * 0.5 * screen_w
    sy = (1.0 - ndc_y) * 0.5 * screen_h
    return (sx, sy)
