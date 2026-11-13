"""Compile and run task code in a temporary workspace with owned child processes.

This is a workspace and timeout boundary, not an OS security sandbox.

Languages supported out of the box:
- python  (cpython3 -I)
- bash    (bash)
- c       (cc -O2 single file, run binary)
- rust    (rustc, cheap one-file)
- node    (node)
- go      (go run)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .store import Store, new_id, now


LANGUAGES = {"python", "bash", "c", "cpp", "rust", "node", "go"}


def run(app, *, language: str, code: str, timeout: int = 60,
        stdin: str | None = None, run_id: str | None = None) -> dict:
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language}")
    store: Store = app.store
    store.require_desktop_owner()
    from .process_runtime import run as run_process
    from .operator_tools import OperatorTools
    check=OperatorTools(app,run_id=run_id).check
    check()

    exec_id = new_id("exec")
    workspace=store.root/'workspaces';workspace.mkdir(parents=True,exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"bh-exec-{exec_id}-",dir=str(workspace)))
    try:
        file_name, compile_cmd, run_cmd = _prepare(language, code, tmp)
        source = tmp / file_name
        source.write_text(code, encoding="utf-8")

        # compile step (if any)
        compile_log = ""
        if compile_cmd:
            compile_proc = run_process(compile_cmd,cwd=str(tmp),timeout=timeout,run_id=run_id,check=check)
            compile_log = compile_proc['stdout'] + compile_proc['stderr']
            if compile_proc['exit_code'] != 0:
                record = {
                    "id": exec_id, "language": language,
                    "status": "compile_failed",
                    "compile_log": compile_log[-8000:],
                    "run_id": run_id, "ts": now(),
                }
                store.put("code_exec", record, id=exec_id)
                return record

        # run step
        start = time.time()
        proc = run_process(run_cmd,cwd=str(tmp),timeout=timeout,input=stdin,run_id=run_id,check=check)
        duration = int((time.time() - start) * 1000)
        record = {
            "id": exec_id, "language": language,
            "status": "ok" if proc['exit_code'] == 0 else "nonzero",
            "exit_code": proc['exit_code'],
            "stdout": proc['stdout'][-16000:],
            "stderr": proc['stderr'][-8000:],
            "compile_log": compile_log[-4000:] if compile_log else "",
            "duration_ms": duration,
            "run_id": run_id, "ts": now(),
        }
        store.put("code_exec", record, id=exec_id)
        if hasattr(app, "timeline"):
            app.timeline.emit(kind="code_exec", phase="complete",
                              run_id=run_id,
                              outcome=record["status"],
                              duration_ms=duration,
                              tool=language,
                              payload={"exit": proc.returncode})
        return record
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prepare(language: str, code: str, tmp: Path) -> tuple[str, list[str] | None, list[str]]:
    """Return (source_name, compile_cmd, run_cmd)."""
    if language == "python":
        python=shutil.which('python3') or shutil.which('python')
        if not python:raise ValueError('Install Python 3 and restart the app to refresh PATH.')
        return "main.py", None, [python, "-I", "main.py"]
    if language == "bash":
        return "main.sh", None, ["bash", "main.sh"]
    if language == "node":
        return "main.js", None, ["node", "main.js"]
    if language == "go":
        return "main.go", None, ["go", "run", "main.go"]
    if language == "c":
        compiler=shutil.which('cc') or shutil.which('gcc')
        if not compiler:raise ValueError('Install a C compiler and restart the app to refresh PATH.')
        binary=str(tmp/('main.exe' if os.name=='nt' else 'main'))
        return "main.c", [compiler, "-O2", "-o", binary, "main.c"], [binary]
    if language == "cpp":
        compiler=shutil.which('c++') or shutil.which('g++')
        if not compiler:raise ValueError('Install a C++ compiler and restart the app to refresh PATH.')
        binary=str(tmp/('main.exe' if os.name=='nt' else 'main'))
        return "main.cpp", [compiler, "-O2", "-std=c++17", "-o", binary, "main.cpp"], [binary]
    if language == "rust":
        binary=str(tmp/('main.exe' if os.name=='nt' else 'main'))
        return "main.rs", ["rustc", "-O", "-o", binary, "main.rs"], [binary]
    raise ValueError(f"unsupported: {language}")
