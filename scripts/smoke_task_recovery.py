"""Exercise restart recovery across real local backend stdio processes.

Usage after setup:
  backend/.venv/bin/python scripts/smoke_task_recovery.py
Or to inspect a packaged sidecar:
  python3 scripts/smoke_task_recovery.py src-tauri/binaries/bughunter-backend-aarch64-apple-darwin

This sends no process-memory command or network request. Only the metadata
reporting endpoint is called. It does not replace a live macOS GUI test.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(executable: str, data: Path):
    if executable == "--python":
        return [sys.executable, "-m", "bughunter", "--stdio", "--data-dir", str(data)]
    binary = Path(executable).expanduser().resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Backend binary not found: {binary}")
    return [str(binary), "--stdio", "--data-dir", str(data)]


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else "--python"
    env = os.environ.copy()
    if binary == "--python":
        env["PYTHONPATH"] = str(ROOT / "backend") + os.pathsep + env.get("PYTHONPATH", "")

    with tempfile.TemporaryDirectory(prefix="bughunter-task-recovery-") as data:
        data_dir = Path(data)

        def start():
            return subprocess.Popen(
                command(binary, data_dir), cwd=ROOT / "backend", env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )

        def rpc(process, sequence, method, **params):
            process.stdin.write(json.dumps({"id": sequence, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            if not select.select([process.stdout], [], [], 30)[0]:
                raise TimeoutError(f"No backend response for {method}")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Backend exited before sending the response")
            data = json.loads(line)
            if "error" in data:
                raise RuntimeError(f"{method}: {data['error']}")
            return data["result"]

        first = start()
        try:
            record = rpc(first, 1, "tasks.external.start", kind="memory_session", title="Restart smoke session")
            task_id = record["id"]
            rpc(first, 2, "tasks.external.event", id=task_id,
                kind="tool_call", name="simulated_read", status="running")
            assert rpc(first, 3, "tasks.get", id=task_id)["status"] == "running"
        finally:
            first.stdin.close()
            first.wait(timeout=20)

        second = start()
        try:
            row = rpc(second, 1, "tasks.get", id=task_id)
            assert row["status"] == "interrupted", row
            assert any(e.get("name") == "simulated_read" for e in row["events"]), row
            assert row["events"][-1]["kind"] == "recovered", row
            try:
                rpc(second, 2, "tasks.retry", id=task_id)
            except RuntimeError as exc:
                assert "replayed" in str(exc) or "Reopen" in str(exc), str(exc)
            else:
                raise AssertionError("An interrupted memory run must never be replayed automatically")
            print("PASS: backend stdio restart retains tool metadata, marks interruption, and forbids replay")
        finally:
            second.stdin.close()
            second.wait(timeout=20)


if __name__ == "__main__":
    main()
