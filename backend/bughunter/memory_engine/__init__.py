"""Portable native-memory engine.

A single stable protocol over a bounded-queue transport, plus pluggable
OS-specific adapters (macOS mach_vm / lldb, Windows ReadProcessMemory /
WriteProcessMemory). Callers get read/write/scan/pointer-walk primitives
bound to a process-birth identity (pid + start_time_ns + target
fingerprint) rather than a bare PID that may be reused.

The legacy ``bughunter.mac_memory`` module is now a thin compatibility
facade over :mod:`bughunter.memory_engine.mac_adapter`.
"""
from __future__ import annotations

from .protocol import (
    DEFAULT_MAX_MESSAGE_SIZE,
    LATEST_FRAME_CAPACITY,
    PRIORITY_STOP,
    PRIORITY_CONTROL,
    PRIORITY_NORMAL,
    MemoryEngineError,
    MemoryEngineProtocolError,
    PartialReadError,
    ProcessIdentity,
    Request,
    Response,
    Notification,
    BoundedWriterQueue,
    LatestFrameChannel,
    PermanentReaderLoop,
    encode_frame,
    decode_frame,
)
from .ownership import (
    AttachBusy,
    ExecutorRegistry,
    OwnerKey,
)
from .server import (
    MemoryEngineServer,
    ServiceConfig,
    ToolController,
)

__all__ = [
    "AttachBusy",
    "BoundedWriterQueue",
    "DEFAULT_MAX_MESSAGE_SIZE",
    "ExecutorRegistry",
    "LatestFrameChannel",
    "LATEST_FRAME_CAPACITY",
    "MemoryEngineError",
    "MemoryEngineProtocolError",
    "MemoryEngineServer",
    "Notification",
    "OwnerKey",
    "PRIORITY_CONTROL",
    "PRIORITY_NORMAL",
    "PRIORITY_STOP",
    "PartialReadError",
    "PermanentReaderLoop",
    "ProcessIdentity",
    "Request",
    "Response",
    "ServiceConfig",
    "ToolController",
    "decode_frame",
    "encode_frame",
]


def available_adapters() -> list[str]:
    """Report which OS adapters import cleanly on this host."""
    found: list[str] = []
    try:
        from . import mac_adapter  # noqa: F401

        found.append("mac")
    except Exception:
        pass
    try:
        from . import windows_adapter  # noqa: F401

        found.append("windows")
    except Exception:
        pass
    return found
