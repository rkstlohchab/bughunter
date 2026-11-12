"""Shared helpers for operator-mode tool adapters."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class ToolMissing(RuntimeError):
    pass


def locate(binary: str, hint: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        msg = f"'{binary}' not found on PATH"
        if hint:
            msg += f" — install hint: {hint}"
        raise ToolMissing(msg)
    return path


def run_cmd(argv: list[str], *, cwd: str | None = None,
            timeout: int = 600, stdin: str | None = None,
            env: dict | None = None) -> dict:
    start = time.time()
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env,
            capture_output=True, text=True, timeout=timeout, input=stdin,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(argv),
            "exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s",
            "duration_ms": int((time.time() - start) * 1000),
            "timeout": True,
        }
    return {
        "command": " ".join(argv),
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[:200_000],
        "stderr": (proc.stderr or "")[:40_000],
        "duration_ms": int((time.time() - start) * 1000),
    }


def envelope(tool: str, result: dict, **extras: Any) -> dict:
    out = {"tool": tool, **result}
    for k, v in extras.items():
        out[k] = v
    return out
