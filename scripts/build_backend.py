"""Freeze the Python runtime into the Tauri sidecar for the current architecture."""
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    python = ROOT/"backend/.venv/bin/python"
    if not python.exists():
        raise SystemExit("Run python3 scripts/setup.py first.")
    triple = subprocess.check_output(["rustc","--print","host-tuple"],text=True).strip()
    output = ROOT/"src-tauri/binaries"
    output.mkdir(parents=True,exist_ok=True)
    command = [str(python),"-m","PyInstaller","--noconfirm","--clean","--onefile","--name","bughunter-backend",
        "--paths",str(ROOT/"backend"),"--distpath",str(ROOT/"build/sidecar"),"--workpath",str(ROOT/"build/pyinstaller"),
        "--specpath",str(ROOT/"build"),"--add-data",f"{ROOT/'prompts/research-assistant.md'}:bughunter/resources"]
    # Keep the user-supplied prompt corpus and its model index available in the
    # frozen sidecar. These are research data, never program instructions.
    corpus = ROOT/'backend/bughunter/assets/prompt_corpus'
    if corpus.is_dir():
        command += ['--add-data', f'{corpus}:bughunter/assets/prompt_corpus']
    for package in ("sqlite_vec","mitmproxy","mitmproxy_rs","yt_dlp","playwright","selenium","keyring","certifi","pymupdf","ddgs"):
        command += ["--collect-all",package]
    command += ["--hidden-import","keyring.backends.macOS","--hidden-import","bughunter.mac_memory",str(ROOT/"scripts/sidecar_entry.py")]
    subprocess.run(command,cwd=ROOT,check=True)
    extension = ".exe" if platform.system()=="Windows" else ""
    source = ROOT/f"build/sidecar/bughunter-backend{extension}"
    destination = output/f"bughunter-backend-{triple}{extension}"
    shutil.copy2(source,destination)
    destination.chmod(0o755)
    print(f"Packaged Python sidecar: {destination}")

if __name__ == "__main__":
    main()
