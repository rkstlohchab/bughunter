"""dma_memread — PCILeech / MemProcFS DMA reader adapter.

Wraps a MemProcFS reader subprocess (``cheats/dma-stack/pcileech_reader.py``).
Supports: probe, start, stop, status, offset_scan modes.

Requires: ``pip install memprocfs`` + ``pcileech`` + an FPGA (CaptainDMA,
LeetDMA, Enigma, etc.) on the operator's second PC. This adapter does NOT
auto-install hardware drivers — it just orchestrates the Python reader.
"""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path

from ._common import envelope, locate


def run(app, *, mode: str = "status",
        pid: int | None = None,
        project_id: str | None = None,
        game: str | None = None,
        process_name: str | None = None,
        offsets_path: str | None = None,
        kmbox_endpoint: str | None = None,
        humanize: bool = True,
        **_) -> dict:
    """Modes: probe · start · stop · status · offset_scan"""
    if mode == "probe":
        return _probe()
    if mode == "start":
        return _start(app, project_id=project_id, game=game,
                       process_name=process_name,
                       offsets_path=offsets_path,
                       kmbox_endpoint=kmbox_endpoint,
                       humanize=humanize)
    if mode == "stop":
        return _stop(pid)
    if mode == "status":
        return _status(app, pid)
    if mode == "offset_scan":
        return _offset_scan(app, game=game, process_name=process_name)
    raise ValueError(f"unknown mode: {mode}")


def _probe() -> dict:
    """Does PCILeech see an FPGA?"""
    bin_ = shutil.which("pcileech")
    if not bin_:
        return envelope("dma_memread",
                        {"command": "pcileech", "exit_code": -1,
                         "stdout": "", "stderr": "pcileech not on PATH",
                         "duration_ms": 0},
                        hardware_ok=False,
                        hint="brew install pcileech  (or build from https://github.com/ufrisk/pcileech)")
    try:
        proc = subprocess.run([bin_, "probe"], capture_output=True, text=True, timeout=15)
        out = proc.stdout + proc.stderr
        has_fpga = any(k in out for k in ("USB3380", "FT601", "Squirrel",
                                          "LeetDMA", "Enigma", "ScreamerM2"))
        return envelope("dma_memread",
                        {"command": f"{bin_} probe", "exit_code": proc.returncode,
                         "stdout": out[:4000], "stderr": "", "duration_ms": 0},
                        hardware_ok=has_fpga)
    except Exception as exc:
        return envelope("dma_memread",
                        {"command": "pcileech probe", "exit_code": -1,
                         "stdout": "", "stderr": str(exc), "duration_ms": 0},
                        hardware_ok=False)


def _start(app, *, project_id, game, process_name, offsets_path,
           kmbox_endpoint, humanize) -> dict:
    if not process_name:
        process_name = _default_process_name(game or "")
    if not process_name:
        raise ValueError("process_name required (or recognized game)")
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "cheats" / "dma-stack" / "pcileech_reader.py"
    if not script.exists():
        raise FileNotFoundError(f"reader script missing at {script}")
    if not offsets_path:
        offsets_path = str(Path(app.store.root) / "gamelab_offsets" / f"{game}.json")
    cfg = {
        "process_name": process_name,
        "offsets_path": offsets_path,
        "kmbox_endpoint": kmbox_endpoint,
        "humanize": humanize,
        "project_id": project_id,
        "game": game,
    }
    log_dir = Path(app.store.root) / "gamelab_logs" / "dma_memread"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / f"run-{int(time.time())}.log"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = ["python3", str(script), "--config-json", json.dumps(cfg)]
    f = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                            env=env, cwd=str(script.parent),
                            start_new_session=True)
    return envelope("dma_memread",
                    {"command": " ".join(cmd), "exit_code": None,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=proc.pid, log_path=str(log_path),
                    process_name=process_name, mode="start")


def _stop(pid: int | None) -> dict:
    if not pid:
        raise ValueError("stop requires pid")
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.killpg(os.getpgid(pid), 0)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    return envelope("dma_memread",
                    {"command": f"kill {pid}", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    pid=pid, mode="stop")


def _status(app, pid: int | None) -> dict:
    if not pid:
        raise ValueError("status requires pid")
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    tail = ""
    log_dir = Path(app.store.root) / "gamelab_logs" / "dma_memread"
    if log_dir.exists():
        logs = sorted(log_dir.glob("run-*.log"))
        if logs:
            text = logs[-1].read_bytes()[-4000:].decode("utf-8", errors="replace")
            tail = text
    return envelope("dma_memread",
                    {"command": "", "exit_code": 0 if alive else -1,
                     "stdout": tail, "stderr": "", "duration_ms": 0},
                    pid=pid, alive=alive)


def _offset_scan(app, *, game, process_name) -> dict:
    """AOB-scan a running process via MemProcFS and emit a fresh offset set."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "cheats" / "dma-stack" / "offset_finder.py"
    if not script.exists():
        raise FileNotFoundError(f"offset_finder missing at {script}")
    sig_path = repo_root / "cheats" / "dma-stack" / "signatures" / f"{game}.sig.json"
    if not sig_path.exists():
        return envelope("dma_memread",
                        {"command": "offset_scan", "exit_code": -1,
                         "stdout": "", "stderr": f"no signature set for {game}",
                         "duration_ms": 0}, offsets={})
    proc = subprocess.run(
        ["python3", str(script), "--process", process_name or _default_process_name(game or ""),
         "--signatures", str(sig_path), "--json"],
        capture_output=True, text=True, timeout=120,
    )
    offsets = {}
    try:
        offsets = json.loads(proc.stdout) if proc.stdout else {}
    except Exception:
        pass
    return envelope("dma_memread",
                    {"command": "offset_scan", "exit_code": proc.returncode,
                     "stdout": proc.stdout[:40000], "stderr": proc.stderr[:4000],
                     "duration_ms": 0}, offsets=offsets)


def _default_process_name(game: str) -> str:
    return {
        "valorant": "VALORANT-Win64-Shipping.exe",
        "cs2": "cs2.exe",
        "apex": "r5apex.exe",
        "rust": "RustClient.exe",
        "fortnite": "FortniteClient-Win64-Shipping.exe",
        "r6_siege": "RainbowSix.exe",
        "tarkov": "EscapeFromTarkov.exe",
        "cod_mw": "ModernWarfare.exe",
    }.get(game, "")
