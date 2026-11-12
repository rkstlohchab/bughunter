"""Generic reader loop — wrap any ``EngineWalker`` with a ticking loop
that emits frames + maintains an FPS stat."""
from __future__ import annotations

import json
import signal
import time
from pathlib import Path
from typing import Callable, Optional

from .engines.base import EngineWalker
from .frame import Frame, FrameEmitter


class ReaderLoop:
    def __init__(self, walker: EngineWalker, emitter: FrameEmitter, *,
                 target_hz: int = 200, screen_w: int = 1920,
                 screen_h: int = 1080):
        self.walker = walker
        self.emitter = emitter
        self.target_hz = target_hz
        self.period = 1.0 / target_hz
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._stop = False
        self._frames = 0
        self._last_fps_stamp = time.monotonic()
        self._last_fps = 0.0

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        last = time.monotonic()
        while not self._stop:
            now = time.monotonic()
            dt = now - last
            if dt < self.period:
                time.sleep(max(0.0, self.period - dt))
            last = time.monotonic()
            try:
                frame = self.walker.build_frame()
                frame.viewport.width = self.screen_w
                frame.viewport.height = self.screen_h
                frame.reader_fps = self._tick_fps()
                self.emitter.emit(frame)
            except Exception as exc:
                err_frame = Frame(
                    game=self.walker.GAME_ID,
                    anticheat=self.walker.ANTICHEAT,
                    status=f"reader_error: {exc!s}"[:200],
                )
                self.emitter.emit(err_frame)
                time.sleep(0.05)
        self.emitter.close()

    def _tick_fps(self) -> float:
        self._frames += 1
        now = time.monotonic()
        if now - self._last_fps_stamp >= 1.0:
            self._last_fps = self._frames / (now - self._last_fps_stamp)
            self._frames = 0
            self._last_fps_stamp = now
        return round(self._last_fps, 1)


# ---------------------------------------------------------------------------


def build_walker(game: str, offsets_path: str, *,
                  reader_backend: str = "dma",
                  process_name: Optional[str] = None,
                  mobile_endpoint: str = "127.0.0.1:65510"
                  ) -> EngineWalker:
    """Factory — look up the per-game reader module."""
    from . import readers
    from .engines.base import Offsets
    off_blob = json.loads(Path(offsets_path).read_text()) if Path(offsets_path).exists() else {}
    offsets = Offsets(data=off_blob.get("offsets", off_blob))

    if reader_backend == "dma":
        from .memprocfs_backend import MemProcFsReader
        reader = MemProcFsReader(process_name=process_name or _default_process(game))
    elif reader_backend == "rpm":
        from .memprocfs_backend import WindowsRpmReader
        reader = WindowsRpmReader(process_name=process_name or _default_process(game))
    elif reader_backend == "android":
        from .engines.android_native import AndroidMemoryBridge
        host, port = mobile_endpoint.split(":", 1)
        reader = AndroidMemoryBridge(host=host, port=int(port))
    else:
        raise ValueError(f"unknown reader_backend: {reader_backend}")

    walker_cls = readers.WALKER_BY_GAME.get(game)
    if not walker_cls:
        raise ValueError(f"no reader for game: {game}")
    return walker_cls(reader=reader, offsets=offsets)


def _default_process(game: str) -> str:
    return {
        "cs2":      "cs2.exe",
        "valorant": "VALORANT-Win64-Shipping.exe",
        "apex":     "r5apex.exe",
        "rust":     "RustClient.exe",
        "fortnite": "FortniteClient-Win64-Shipping.exe",
        "r6_siege": "RainbowSix.exe",
        "tarkov":   "EscapeFromTarkov.exe",
        "cod_mw":   "ModernWarfare.exe",
        "pubg":     "TslGame.exe",
        "bgmi":     "com.pubg.imobile",
        "cod_mobile": "com.activision.callofduty.shooter",
        "free_fire": "com.dts.freefireth",
    }.get(game, "")
