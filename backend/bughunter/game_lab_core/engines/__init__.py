"""Engine walkers.

An engine walker knows how to iterate a game's entity list, read bone
positions, resolve team/HP/name, and project world to screen. One
walker per game engine; per-game offset tables plug into the walker.

Memory access is abstracted: the walker takes a ``MemoryReader``
object (``.read_u8/u16/u32/u64/i32/f32/vec3/mat44/bytes``) so the same
walker works over DMA (MemProcFS), external RPM (OpenProcess), or the
mobile ADB-forward bridge.
"""
from .base import EngineWalker, MemoryReader, Offsets  # noqa: F401
