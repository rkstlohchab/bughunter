"""Android native walker — reads game memory on a rooted phone through a
Zygisk hook, piped over ADB forward.

The Zygisk module (``cheats/mobile-bgmi/zygisk_memory/``) opens a UNIX
socket inside the game's process, reads the entity list via standard
``/proc/self/mem``-style libc reads, and ships frames out through
``adb forward tcp:<port> localabstract:gamelab_mem``.

This walker is slightly different from the PC walkers — the Zygisk side
already builds the entity array and emits structured data. The walker
just consumes it and wraps it in a ``Frame``.
"""
from __future__ import annotations

import json
import socket
from typing import Optional

from ..frame import Entity, Frame, ViewMatrix, Viewport
from .base import EngineWalker, MemoryReader, Offsets


class AndroidMemoryBridge(MemoryReader):
    """Transparent MemoryReader for consumers that expect one. Not used by
    ``AndroidNativeWalker`` — the Zygisk side pre-digests everything."""

    def __init__(self, host="127.0.0.1", port=65510):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(1.0)
        self.sock.connect((host, port))

    def read(self, address: int, size: int) -> bytes:
        req = {"cmd": "read", "addr": address, "size": size}
        self.sock.sendall((json.dumps(req) + "\n").encode())
        n = int.from_bytes(self.sock.recv(4), "little", signed=False)
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk: break
            buf += chunk
        return buf

    def resolve_module(self, name: str) -> int:
        req = {"cmd": "module", "name": name}
        self.sock.sendall((json.dumps(req) + "\n").encode())
        reply = _recv_line(self.sock)
        return int(json.loads(reply).get("base", 0))

    def close(self):
        try: self.sock.close()
        except Exception: pass


class AndroidNativeWalker(EngineWalker):
    GAME_ID = "bgmi"
    ANTICHEAT = "bgmi-ace"
    MATRIX_ORDER = "row"

    def __init__(self, reader: AndroidMemoryBridge, offsets: Offsets):
        super().__init__(reader, offsets)
        self.r: AndroidMemoryBridge = reader

    def resolve_base(self) -> int:
        if not self._base:
            self._base = self.r.resolve_module(
                self.off.get("Module", "libUE4.so"))
        return self._base

    def read_viewport(self) -> Viewport:
        return Viewport(width=2340, height=1080, fov_x=100.0, fov_y=55.0)

    def read_view_matrix(self) -> ViewMatrix:
        """The Zygisk side ships the view matrix inline with each frame."""
        self.r.sock.sendall(b'{"cmd":"view"}\n')
        reply = _recv_line(self.r.sock)
        d = json.loads(reply)
        return ViewMatrix(data=d.get("matrix", [0.0] * 16))

    def read_local_player(self) -> Optional[Entity]:
        self.r.sock.sendall(b'{"cmd":"local"}\n')
        reply = _recv_line(self.r.sock)
        d = json.loads(reply)
        if not d.get("ok"):
            return None
        return Entity(
            id=d.get("id", 0), kind="player",
            team=d.get("team", 0), hp=d.get("hp", 100),
            head_world=tuple(d.get("head", (0, 0, 0))),
            feet_world=tuple(d.get("feet", (0, 0, 0))),
            center_world=tuple(d.get("center", (0, 0, 0))),
            bones_world=[tuple(b) for b in d.get("bones", [])],
            is_local=True,
        )

    def iter_players(self) -> list[Entity]:
        self.r.sock.sendall(b'{"cmd":"players"}\n')
        reply = _recv_line(self.r.sock)
        d = json.loads(reply)
        out: list[Entity] = []
        for p in d.get("players", []):
            out.append(Entity(
                id=p.get("id", 0), kind="player",
                team=p.get("team", 0), hp=p.get("hp", 100),
                name=p.get("name", ""),
                head_world=tuple(p.get("head", (0, 0, 0))),
                feet_world=tuple(p.get("feet", (0, 0, 0))),
                center_world=tuple(p.get("center", (0, 0, 0))),
                bones_world=[tuple(b) for b in p.get("bones", [])],
                visible=p.get("visible", False),
                extra={"weapon": p.get("weapon", "")},
            ))
        return out

    def iter_loot(self) -> list[Entity]:
        self.r.sock.sendall(b'{"cmd":"loot"}\n')
        try:
            reply = _recv_line(self.r.sock)
        except Exception:
            return []
        d = json.loads(reply)
        out: list[Entity] = []
        for item in d.get("items", []):
            out.append(Entity(
                id=item.get("id", 0), kind="loot",
                name=item.get("name", ""),
                center_world=tuple(item.get("pos", (0, 0, 0))),
                feet_world=tuple(item.get("pos", (0, 0, 0))),
                head_world=tuple(item.get("pos", (0, 0, 0))),
            ))
        return out


def _recv_line(sock: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.decode("utf-8", errors="replace").strip()
