"""bloodhound / sharphound collector adapter.

Wraps the ``bloodhound-python`` collector for cross-platform runs. Output
is a ZIP of JSON files per data type, which this adapter returns the path
to; actual ingestion happens in the BloodHound GUI (the model doesn't
need to parse the graph here).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, domain: str, dc: str,
        username: str, password: str | None = None,
        collection: str = "Default",
        extra_args: list[str] | None = None,
        max_duration: int = 1800, **_) -> dict:
    binary = locate("bloodhound-python",
                    hint="pip install bloodhound")
    out_dir = Path(app.store.root) / "workspaces" / f"bh-{int(__import__('time').time())}"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    argv = [binary, "-d", domain, "-dc", dc, "-u", username,
            "-c", collection, "-op", str(out_dir)]
    if password:
        argv += ["-p", password]
    argv += list(extra_args or [])
    result = run_cmd(argv, timeout=max_duration)
    produced = sorted(str(p) for p in out_dir.glob("*.json"))
    return envelope("bloodhound", result,
                    output_dir=str(out_dir), files=produced,
                    loot=[{"kind": "bloodhound-collection",
                           "path": str(out_dir),
                           "notes": f"{domain} via {username}"}])
