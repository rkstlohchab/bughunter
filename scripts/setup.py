"""Install project-local dependencies. Does not alter system or Burp settings."""
import shutil
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    for executable in ("uv","node","npm","cargo"):
        if not shutil.which(executable):
            raise SystemExit(f"Install {executable} first, then run this script again.")
    subprocess.run(["uv","sync","--python","3.12","--extra","dev","--extra","tools","--extra","browser"],cwd=ROOT/"backend",check=True)
    subprocess.run(["npm","ci"],cwd=ROOT,check=True)
    subprocess.run([str(ROOT/"backend/.venv/bin/python"),"-m","playwright","install","chromium"],check=True)
    if platform.system() == "Darwin":
        # Compile local trusted source; never download a prebuilt automation binary.
        subprocess.run(["bash",str(ROOT/"scripts/build_mac_axbridge.sh")],cwd=ROOT,check=True)
    print("Dependencies installed. Run python3 scripts/dev.py or npm run desktop:dev.")

if __name__ == "__main__":
    main()
