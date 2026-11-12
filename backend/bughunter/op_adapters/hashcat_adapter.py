"""hashcat adapter — GPU password cracking.

Operator mode only; raw hash lists are sensitive and must come from
operator-owned engagements (lab dumps, their own creds, authorized
assessment data).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


HASH_MODES = {
    "md5": 0, "sha1": 100, "sha256": 1400, "sha512": 1700,
    "ntlm": 1000, "ntlmv2": 5600, "lm": 3000,
    "bcrypt": 3200, "pbkdf2-sha256": 10900,
    "wpa-pmkid-pbkdf2": 22000, "wpa-eapol-pbkdf2": 2500,
    "linux-sha512crypt": 1800, "mssql-2012": 1731,
    "krbtgt-23": 13100, "krbtgt-18": 19700, "mysql": 300,
}


def run(app, *, hash_file: str, wordlist: str,
        mode: str = "md5", attack: str = "straight",
        rules: list[str] | None = None,
        workload: int = 2,
        extra_args: list[str] | None = None,
        max_duration: int = 3600, **_) -> dict:
    binary = locate("hashcat", hint="brew install hashcat")
    hf = Path(hash_file).expanduser()
    wl = Path(wordlist).expanduser()
    if not hf.exists():
        raise ValueError(f"hash file not found: {hf}")
    if not wl.exists():
        raise ValueError(f"wordlist not found: {wl}")
    if mode not in HASH_MODES:
        raise ValueError(f"unknown mode '{mode}'; add to HASH_MODES to support")
    pot = Path(app.store.root) / "workspaces" / "hashcat.potfile"
    pot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    attack_map = {"straight": "0", "combinator": "1", "mask": "3", "hybrid-wm": "6"}
    argv = [binary, "-m", str(HASH_MODES[mode]),
            "-a", attack_map.get(attack, "0"),
            "-w", str(workload),
            "--potfile-path", str(pot),
            "--quiet", str(hf), str(wl)]
    for rule in (rules or []):
        argv += ["-r", rule]
    argv += list(extra_args or [])

    result = run_cmd(argv, timeout=max_duration)
    # show cracked
    show = run_cmd([binary, "-m", str(HASH_MODES[mode]),
                    "--potfile-path", str(pot), "--show", str(hf)],
                   timeout=60)
    cracked = []
    for line in (show["stdout"] or "").splitlines():
        if ":" in line:
            h, _, p = line.partition(":")
            cracked.append({"hash": h.strip(), "plaintext": p.strip()})
    return envelope("hashcat", result, cracked=cracked,
                    pot_path=str(pot))
