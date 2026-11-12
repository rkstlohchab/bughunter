"""GameLab core — universal memory-ESP framework.

Pieces:
- ``frame``: universal frame schema every reader emits
- ``engines``: per-engine walker interfaces (Source2, Unreal, Unity IL2CPP,
  Apex-Source, AnvilNext)
- ``readers``: per-game reader modules that compose an engine walker + a
  per-game offset table
- ``reader_loop``: generic read loop that emits frames to a target
  (shared file, socket, or in-process callback)

Every reader emits the same ``Frame`` so one overlay renderer draws for every
game. New games plug in by adding a reader module; no overlay changes needed.
"""
from .frame import Frame, Entity, Viewport, ViewMatrix, FrameEmitter  # noqa: F401
