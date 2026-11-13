"""Freeze the Python runtime into the Tauri sidecar for the current architecture."""
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    python = ROOT/("backend/.venv/Scripts/python.exe" if platform.system()=="Windows" else "backend/.venv/bin/python")
    if not python.exists():
        raise SystemExit("Run python3 scripts/setup.py first.")
    triple = subprocess.check_output(["rustc","--print","host-tuple"],text=True).strip()
    output = ROOT/"src-tauri/binaries"
    output.mkdir(parents=True,exist_ok=True)
    command = [str(python),"-m","PyInstaller","--noconfirm","--clean","--onefile","--name","bughunter-backend",
        "--paths",str(ROOT/"backend"),"--distpath",str(ROOT/"build/sidecar"),"--workpath",str(ROOT/"build/pyinstaller"),
        "--specpath",str(ROOT/"build"),"--add-data",f"{ROOT/'prompts/research-assistant.md'}:bughunter/resources"]
    command += ['--add-data', f"{ROOT/'backend/bughunter/assets/retired_prompt_hashes.json'}:bughunter/assets"]
    references = ROOT/'backend/bughunter/assets/reference_pack'
    command += ['--add-data', f'{references}:bughunter/assets/reference_pack']
    command += ['--add-data', f"{ROOT/'backend/bughunter/assets/agent_skills'}:bughunter/assets/agent_skills"]
    for package in ("sqlite_vec","mitmproxy","mitmproxy_rs","yt_dlp","playwright","selenium","keyring","certifi","pymupdf","ddgs"):
        command += ["--collect-all",package]
    if platform.system()=="Darwin":command += ["--hidden-import","keyring.backends.macOS","--hidden-import","bughunter.mac_memory"]
    elif platform.system()=="Windows":command += ["--hidden-import","keyring.backends.Windows"]
    command += [str(ROOT/"scripts/sidecar_entry.py")]
    subprocess.run(command,cwd=ROOT,check=True)
    extension = ".exe" if platform.system()=="Windows" else ""
    source = ROOT/f"build/sidecar/bughunter-backend{extension}"
    destination = output/f"bughunter-backend-{triple}{extension}"
    shutil.copy2(source,destination)
    destination.chmod(0o755)
    print(f"Packaged Python sidecar: {destination}")

if __name__ == "__main__":
    main()
