"""hwid_spoof — HWID surface reporting + change-detection.

This adapter does NOT write kernel memory or flash firmware. It is a
read-only reporter for the HWID surfaces anticheat reads, plus a
helper to diff against a baseline so the operator can verify a
third-party spoofer did what it claimed.

Writable spoof is out of scope here — too destructive to run from an
agent. GameLab points the operator at per-OS spoofer docs.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

from ._common import envelope


def run(app, *, mode: str = "snapshot", baseline_id: str | None = None,
        label: str | None = None, **_) -> dict:
    """Modes: snapshot · diff · list_baselines"""
    if mode == "snapshot":
        return _snapshot(app, label=label)
    if mode == "diff":
        if not baseline_id:
            raise ValueError("diff requires baseline_id")
        return _diff(app, baseline_id=baseline_id)
    if mode == "list_baselines":
        return _list_baselines(app)
    raise ValueError(f"unknown mode: {mode}")


def _snapshot(app, label: str | None) -> dict:
    sysname = platform.system()
    snap = {"platform": sysname, "label": label, "fields": {}}
    if sysname == "Windows":
        _windows_fields(snap["fields"])
    elif sysname == "Darwin":
        _darwin_fields(snap["fields"])
    elif sysname == "Linux":
        _linux_fields(snap["fields"])
    snap["hash"] = _hash_fields(snap["fields"])
    # persist
    bid = _new_id()
    snap["id"] = bid
    snap["timestamp"] = _now_iso()
    _save_baseline(app, bid, snap)
    return envelope("hwid_spoof",
                    {"command": "snapshot", "exit_code": 0,
                     "stdout": json.dumps(snap["fields"], indent=2),
                     "stderr": "", "duration_ms": 0},
                    hwid_snapshot=snap)


def _diff(app, baseline_id: str) -> dict:
    baseline = _load_baseline(app, baseline_id)
    if not baseline:
        return envelope("hwid_spoof",
                        {"command": "diff", "exit_code": -1,
                         "stdout": "", "stderr": "no such baseline",
                         "duration_ms": 0}, diff=[])
    current = _snapshot(app, label=None)
    curf = current["hwid_snapshot"]["fields"]
    oldf = baseline["fields"]
    keys = sorted(set(curf) | set(oldf))
    diffs = []
    for k in keys:
        a = oldf.get(k)
        b = curf.get(k)
        if a != b:
            diffs.append({"field": k, "before": a, "after": b})
    return envelope("hwid_spoof",
                    {"command": "diff", "exit_code": 0,
                     "stdout": f"{len(diffs)} fields changed",
                     "stderr": "", "duration_ms": 0},
                    diff=diffs, baseline_id=baseline_id)


def _list_baselines(app) -> dict:
    rows = _all_baselines(app)
    return envelope("hwid_spoof",
                    {"command": "list_baselines", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    baselines=[{"id": r["id"], "label": r.get("label"),
                                "timestamp": r.get("timestamp"),
                                "hash": r.get("hash")} for r in rows])


# ---- OS-specific field collectors --------------------------------------


def _run(argv: list[str], timeout: int = 8) -> str:
    try:
        return subprocess.check_output(argv, timeout=timeout,
                                        text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def _windows_fields(out: dict) -> None:
    out["smbios_uuid"] = _ps("(Get-CimInstance Win32_ComputerSystemProduct).UUID")
    out["smbios_serial"] = _ps("(Get-CimInstance Win32_BIOS).SerialNumber")
    out["baseboard_serial"] = _ps("(Get-CimInstance Win32_BaseBoard).SerialNumber")
    out["chassis_serial"] = _ps("(Get-CimInstance Win32_SystemEnclosure).SerialNumber")
    out["machine_guid"] = _ps(r"(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid")
    out["product_id"] = _ps(r"(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').ProductId")
    out["disk_serials"] = _ps(
        "Get-PhysicalDisk | Select -Expand SerialNumber | ForEach-Object { $_.Trim() }"
    ).split("\n")
    out["nic_macs"] = _ps(
        "Get-NetAdapter -Physical | Select -Expand MacAddress"
    ).split("\n")
    out["gpu_names"] = _ps(
        "(Get-CimInstance Win32_VideoController).Name"
    ).split("\n")


def _darwin_fields(out: dict) -> None:
    sp = _run(["system_profiler", "SPHardwareDataType"])
    out["model"] = _m(sp, r"Model Identifier:\s*(.+)")
    out["smbios_uuid"] = _m(sp, r"Hardware UUID:\s*(.+)")
    out["serial"] = _m(sp, r"Serial Number \(system\):\s*(.+)")
    out["processor"] = _m(sp, r"Chip:\s*(.+)")
    ifs = _run(["ifconfig"])
    out["nic_macs"] = re.findall(r"ether ([0-9a-f:]{17})", ifs)
    out["gpu_names"] = re.findall(
        r"Chipset Model:\s*(.+)",
        _run(["system_profiler", "SPDisplaysDataType"]))
    out["disk_serials"] = re.findall(
        r"USB Serial Number:\s*(.+)",
        _run(["system_profiler", "SPNVMeDataType"]))


def _linux_fields(out: dict) -> None:
    def rd(p: str) -> str:
        try:
            return Path(p).read_text().strip()
        except Exception:
            return ""
    out["smbios_uuid"] = rd("/sys/class/dmi/id/product_uuid")
    out["smbios_serial"] = rd("/sys/class/dmi/id/product_serial")
    out["baseboard_serial"] = rd("/sys/class/dmi/id/board_serial")
    out["chassis_serial"] = rd("/sys/class/dmi/id/chassis_serial")
    out["machine_id"] = rd("/etc/machine-id")
    ifs = _run(["ip", "link", "show"])
    out["nic_macs"] = re.findall(r"link/ether ([0-9a-f:]{17})", ifs)
    lsblk = _run(["lsblk", "-dn", "-o", "SERIAL"])
    out["disk_serials"] = [l.strip() for l in lsblk.splitlines() if l.strip()]
    gpus = _run(["lspci", "-nnk"])
    out["gpu_names"] = re.findall(r"VGA.*?\[(.+?)\]", gpus)


def _ps(script: str) -> str:
    r = _run(["powershell", "-NoProfile", "-Command", script])
    return r.strip()


def _m(blob: str, pat: str) -> str:
    m = re.search(pat, blob)
    return m.group(1).strip() if m else ""


# ---- Persistence --------------------------------------------------------


def _save_baseline(app, bid: str, snap: dict) -> None:
    app.store.put("hwid_baseline", snap, id=bid)


def _load_baseline(app, bid: str) -> dict | None:
    return app.store.get("hwid_baseline", bid)


def _all_baselines(app) -> list[dict]:
    return list(app.store.list("hwid_baseline"))


def _hash_fields(fields: dict) -> str:
    serial = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(serial.encode()).hexdigest()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
