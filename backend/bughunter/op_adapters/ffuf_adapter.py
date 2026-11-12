"""ffuf adapter — web content discovery with JSON output."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, url: str, wordlist: str,
        extensions: list[str] | None = None,
        match_codes: list[int] | None = None,
        filter_codes: list[int] | None = None,
        threads: int = 40, delay: str | None = None,
        headers: dict | None = None,
        extra_args: list[str] | None = None,
        max_duration: int = 1200, **_) -> dict:
    binary = locate("ffuf", hint="brew install ffuf")
    if "FUZZ" not in url:
        raise ValueError("ffuf URL must contain FUZZ")
    wl = Path(wordlist).expanduser()
    if not wl.exists():
        raise ValueError(f"wordlist not found: {wl}")
    import tempfile
    tf = Path(tempfile.mkstemp(prefix="ffuf-", suffix=".json")[1])
    argv = [binary, "-u", url, "-w", str(wl),
            "-o", str(tf), "-of", "json", "-t", str(threads)]
    if extensions:
        argv += ["-e", ",".join(extensions)]
    if match_codes:
        argv += ["-mc", ",".join(str(c) for c in match_codes)]
    if filter_codes:
        argv += ["-fc", ",".join(str(c) for c in filter_codes)]
    if delay:
        argv += ["-p", delay]
    for k, v in (headers or {}).items():
        argv += ["-H", f"{k}: {v}"]
    argv += list(extra_args or [])

    result = run_cmd(argv, timeout=max_duration)
    findings = []
    if tf.exists():
        try:
            data = json.loads(tf.read_text())
            findings = data.get("results", [])
        except Exception:
            findings = []
        finally:
            try:
                tf.unlink()
            except Exception:
                pass
    return envelope("ffuf", result, findings=findings)
