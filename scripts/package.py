"""Build a locally installable macOS app bundle."""
import subprocess
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    subprocess.run([sys.executable,str(ROOT/"scripts/build_backend.py")],cwd=ROOT,check=True)
    subprocess.run(["npm","run","desktop:build"],cwd=ROOT,check=True)
    if platform.system() == "Darwin":
        bundle = ROOT/"src-tauri/target/release/bundle/macos/BugHunterClaudex.app"
        subprocess.run(["codesign","--force","--deep","--sign","-",str(bundle)],check=True)
        subprocess.run(["codesign","--verify","--deep","--strict",str(bundle)],check=True)
    print(f"Application: {ROOT/'src-tauri/target/release/bundle/macos/BugHunterClaudex.app'}")
