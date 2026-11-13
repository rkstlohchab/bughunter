"""Shared helpers for operator-mode tool adapters."""
from __future__ import annotations

import shutil
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any


class ToolMissing(RuntimeError):
    pass

_CONTEXT=ContextVar('operator_process_context',default=(None,None))

@contextmanager
def command_context(run_id,check):
    token=_CONTEXT.set((run_id,check))
    try:yield
    finally:_CONTEXT.reset(token)


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
        from ..process_runtime import run
        run_id,check=_CONTEXT.get()
        proc = run(argv,cwd=cwd,env=env,timeout=timeout,input=stdin,run_id=run_id,check=check)
    except TimeoutError:
        return {
            "command": " ".join(argv),
            "exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s",
            "duration_ms": int((time.time() - start) * 1000),
            "timeout": True,
        }
    return {
        "command": " ".join(argv),
        "exit_code": proc['exit_code'],
        "stdout": proc['stdout'][:200_000],
        "stderr": proc['stderr'][:40_000],
        "duration_ms": int((time.time() - start) * 1000),
    }


def envelope(tool: str, result: dict, **extras: Any) -> dict:
    out = {"tool": tool, **result}
    for k, v in extras.items():
        out[k] = v
    return out
