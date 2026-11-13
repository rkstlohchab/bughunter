"""Ensure the current Mac architecture's Python sidecar exists before Tauri bundles it.

A successful one-shot setup builds the sidecar. This hook is useful if the user
edits backend code later and runs only `npm run desktop:build`.
"""
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def sidecar_out() -> Path:
    triple = subprocess.check_output(["rustc", "--print", "host-tuple"], text=True).strip()
    if not triple:
        raise RuntimeError("rustc did not return a host triple")
    suffix='.exe' if platform.system()=='Windows' else ''
    return ROOT / "src-tauri" / "binaries" / f"bughunter-backend-{triple}{suffix}"


def needs_rebuild(output: Path) -> bool:
    if not output.is_file() or output.stat().st_size == 0:
        return True
    stamp = output.stat().st_mtime
    sources = [
        ROOT / "scripts" / "build_backend.py",
        ROOT / "scripts" / "sidecar_entry.py",
        ROOT / "backend" / "pyproject.toml",
        ROOT / "prompts" / "research-assistant.md",
    ]
    sources.extend((BACKEND / "bughunter").rglob("*.py"))
    sources.extend((BACKEND / "bughunter" / "assets").rglob("*"))
    return any(f.is_file() and f.stat().st_mtime > stamp for f in sources)


def main() -> None:
    output = sidecar_out()
    if not needs_rebuild(output):
        print(f"Backend sidecar ready: {output.name}")
        return
    python = BACKEND / (".venv/Scripts/python.exe" if platform.system()=='Windows' else ".venv/bin/python")
    if not python.exists():
        raise SystemExit("Backend environment missing. Run 'bash setup-mac.sh' once first.")
    print("Preparing updated Python backend for the Tauri bundle...")
    subprocess.run([str(python), str(ROOT / "scripts" / "build_backend.py")], cwd=ROOT, check=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit("PyInstaller finished but the backend sidecar is missing.")
    print(f"Backend sidecar ready: {output.name}")


if __name__ == "__main__":
    main()
