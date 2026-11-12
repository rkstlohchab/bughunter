"""radare2 adapter — binary analysis via r2 commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, target: str,
        commands: list[str] | None = None,
        script: str | None = None,
        analysis: str = "aaa",
        extra_args: list[str] | None = None,
        max_duration: int = 1200, **_) -> dict:
    binary = locate("r2", hint="brew install radare2")
    tgt = Path(target).expanduser()
    if not tgt.exists():
        raise ValueError(f"binary not found: {tgt}")
    cmds = [analysis] + (commands or []) + ["q"]
    cmd_str = ";".join(cmds)
    argv = [binary, "-q", "-A", "-c", cmd_str, str(tgt)]
    if script:
        argv += ["-i", str(Path(script).expanduser())]
    argv += list(extra_args or [])
    return envelope("radare2", run_cmd(argv, timeout=max_duration),
                    target=str(tgt))
