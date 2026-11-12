"""ghidra adapter — headless analysis via analyzeHeadless."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, target: str,
        project_name: str = "bughunter",
        script: str | None = None,
        script_args: list[str] | None = None,
        ghidra_install: str | None = None,
        extra_args: list[str] | None = None,
        max_duration: int = 3600, **_) -> dict:
    # analyzeHeadless usually comes from GHIDRA_INSTALL_DIR/support
    install = Path(ghidra_install or "/opt/ghidra")
    headless = install / "support" / "analyzeHeadless"
    if not headless.exists():
        # try discovering via PATH wrapper
        try:
            path = locate("analyzeHeadless", hint="install Ghidra and add support/ to PATH")
            headless = Path(path)
        except Exception:
            raise RuntimeError("analyzeHeadless not found; set ghidra_install arg")
    tgt = Path(target).expanduser()
    if not tgt.exists():
        raise ValueError(f"binary not found: {tgt}")
    import tempfile
    proj_dir = Path(tempfile.mkdtemp(prefix="ghidra-", dir=str(app.store.root / "workspaces")))
    argv = [str(headless), str(proj_dir), project_name,
            "-import", str(tgt), "-overwrite", "-deleteProject"]
    if script:
        argv += ["-postScript", str(Path(script).expanduser())]
        for a in (script_args or []):
            argv += [str(a)]
    argv += list(extra_args or [])
    result = run_cmd(argv, timeout=max_duration)
    try:
        shutil.rmtree(proj_dir, ignore_errors=True)
    except Exception:
        pass
    return envelope("ghidra", result, target=str(tgt))
