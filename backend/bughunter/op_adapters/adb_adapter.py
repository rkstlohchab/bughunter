"""adb adapter — Android debug bridge wrapper for mobile RE."""
from __future__ import annotations

from typing import Any

from ._common import locate, run_cmd, envelope


ALLOWED_VERBS = {
    "devices", "shell", "logcat", "pull", "push", "install",
    "uninstall", "forward", "reverse", "backup", "restore",
    "emu", "wait-for-device", "root", "unroot",
}


def run(app, *, verb: str, args: list[str] | None = None,
        device: str | None = None,
        timeout: int = 300, **_) -> dict:
    if verb not in ALLOWED_VERBS:
        raise ValueError(f"adb verb not allowed: {verb}")
    binary = locate("adb", hint="brew install --cask android-platform-tools")
    argv = [binary]
    if device:
        argv += ["-s", device]
    argv += [verb] + list(args or [])
    return envelope("adb", run_cmd(argv, timeout=timeout),
                    verb=verb, device=device)
