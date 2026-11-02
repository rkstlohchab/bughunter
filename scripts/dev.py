"""Launch the development UI with an authenticated loopback RPC bridge."""
import os
import secrets
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    env = dict(os.environ)
    env["BUGHUNTER_RPC_TOKEN"] = env.get("BUGHUNTER_RPC_TOKEN") or secrets.token_urlsafe(32)
    env["VITE_RPC_TOKEN"] = env["BUGHUNTER_RPC_TOKEN"]
    env.setdefault("BUGHUNTER_DATA_DIR",str(Path.home()/"Library/Application Support/BugHunterClaudex-Development"))
    env["VITE_RPC_URL"] = "http://127.0.0.1:8765"
    python = ROOT/"backend/.venv/bin/python"
    if not python.exists():
        raise SystemExit("Run python3 scripts/setup.py first.")
    backend = subprocess.Popen([str(python),"-m","bughunter","--http","--port","8765"],cwd=ROOT/"backend",env=env)
    frontend = subprocess.Popen(["npm","run","dev"],cwd=ROOT,env=env)
    processes = (backend,frontend)
    def stop(*_):
        for process in processes:
            if process.poll() is None:
                process.terminate()
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    print("Workbench: http://127.0.0.1:1420 (development data is separate from the desktop app)")
    try:
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    finally:
        stop()
        for process in processes:
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill()

if __name__ == "__main__":
    main()
