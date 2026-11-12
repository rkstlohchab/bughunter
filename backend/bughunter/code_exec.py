"""Compile + run sandbox for operator-written code.

Scoped tmpdir under the data directory, subprocess with rlimits, no
network (we don't enable it; OS routes if available), configurable
language runtime.

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
import resource
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

    exec_id = new_id("exec")
    tmp = Path(tempfile.mkdtemp(prefix=f"bh-exec-{exec_id}-",
                                dir=str(store.root / "workspaces")))
    try:
        file_name, compile_cmd, run_cmd = _prepare(language, code, tmp)
        source = tmp / file_name
        source.write_text(code, encoding="utf-8")

        # compile step (if any)
        compile_log = ""
        if compile_cmd:
            compile_proc = subprocess.run(
                compile_cmd, cwd=str(tmp), capture_output=True, text=True,
                timeout=timeout,
            )
            compile_log = compile_proc.stdout + compile_proc.stderr
            if compile_proc.returncode != 0:
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
        proc = subprocess.run(
            run_cmd, cwd=str(tmp),
            capture_output=True, text=True, timeout=timeout,
            input=stdin, preexec_fn=_rlimits,
        )
        duration = int((time.time() - start) * 1000)
        record = {
            "id": exec_id, "language": language,
            "status": "ok" if proc.returncode == 0 else "nonzero",
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-16000:],
            "stderr": proc.stderr[-8000:],
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
        return "main.py", None, ["python3", "-I", "main.py"]
    if language == "bash":
        return "main.sh", None, ["bash", "main.sh"]
    if language == "node":
        return "main.js", None, ["node", "main.js"]
    if language == "go":
        return "main.go", None, ["go", "run", "main.go"]
    if language == "c":
        return "main.c", ["cc", "-O2", "-o", "main", "main.c"], ["./main"]
    if language == "cpp":
        return "main.cpp", ["c++", "-O2", "-std=c++17", "-o", "main", "main.cpp"], ["./main"]
    if language == "rust":
        return "main.rs", ["rustc", "-O", "-o", "main", "main.rs"], ["./main"]
    raise ValueError(f"unsupported: {language}")


def _rlimits() -> None:
    # subprocess preexec on the child only — not available on Windows.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))  # 2 GB
        resource.setrlimit(resource.RLIMIT_CPU, (90, 120))                      # cpu seconds
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 ** 2, 64 * 1024 ** 2))
    except Exception:
        pass
