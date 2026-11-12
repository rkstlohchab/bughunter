"""frida adapter — dynamic instrumentation of mobile / native processes.

Thin wrapper around the ``frida`` CLI. Scripts are loaded from a file
path the operator chooses; this adapter does not synthesize scripts
on behalf of the model (operator pulls them from the training corpus
or hand-writes them).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, mode: str = "list",
        device: str | None = None,
        process: str | None = None,
        script: str | None = None,
        attach: bool = False,
        extra_args: list[str] | None = None,
        max_duration: int = 300, **_) -> dict:
    """Modes:
    - list        : `frida-ps -D <device>`
    - attach      : `frida -D <device> -n <process> -l <script>`
    - spawn       : `frida -D <device> -f <process> -l <script>`
    """
    if mode == "list":
        binary = locate("frida-ps", hint="pip install frida-tools")
        argv = [binary]
        if device:
            argv += ["-D", device]
        return envelope("frida", run_cmd(argv, timeout=max_duration))
    binary = locate("frida", hint="pip install frida-tools")
    if not process:
        raise ValueError("frida attach/spawn requires process")
    if script is None:
        raise ValueError("frida attach/spawn requires a script path")
    script_path = Path(script).expanduser()
    if not script_path.exists():
        raise ValueError(f"script not found: {script_path}")
    argv = [binary]
    if device:
        argv += ["-D", device]
    if mode == "attach":
        argv += ["-n", process]
    elif mode == "spawn":
        argv += ["-f", process]
    else:
        raise ValueError(f"unknown mode: {mode}")
    argv += ["-l", str(script_path), "--no-pause"]
    argv += list(extra_args or [])
    return envelope("frida", run_cmd(argv, timeout=max_duration))
