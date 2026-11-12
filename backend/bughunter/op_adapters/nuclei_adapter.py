"""nuclei adapter — vulnerability template engine with JSONL output.

Extends the existing bounded ``adapters.ToolService`` path. Operator mode
exposes the full template corpus, not just the single reviewed response-
metadata template.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, targets: list[str] | str,
        templates: list[str] | None = None,
        tags: list[str] | None = None,
        severity: list[str] | None = None,
        rate_limit: int = 50,
        concurrency: int = 25,
        headers: dict | None = None,
        extra_args: list[str] | None = None,
        max_duration: int = 1800,
        project_id: str | None = None, **_) -> dict:
    binary = locate("nuclei",
                    hint="go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest")
    if isinstance(targets, str):
        targets_list = [t.strip() for t in targets.splitlines() if t.strip()]
    else:
        targets_list = list(targets)
    if not targets_list:
        raise ValueError("nuclei requires targets")
    import tempfile
    tf = Path(tempfile.mkstemp(prefix="nuclei-tgt-", suffix=".txt")[1])
    tf.write_text("\n".join(targets_list))
    out = Path(tempfile.mkstemp(prefix="nuclei-", suffix=".jsonl")[1])
    argv = [binary, "-l", str(tf), "-jsonl", "-o", str(out),
            "-rl", str(rate_limit), "-c", str(concurrency),
            "-silent", "-disable-update-check"]
    for t in (templates or []):
        argv += ["-t", t]
    if tags:
        argv += ["-tags", ",".join(tags)]
    if severity:
        argv += ["-s", ",".join(severity)]
    for k, v in (headers or {}).items():
        argv += ["-H", f"{k}: {v}"]
    argv += list(extra_args or [])

    result = run_cmd(argv, timeout=max_duration)
    findings: list[dict] = []
    if out.exists():
        try:
            for line in out.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    findings.append(json.loads(line))
                except Exception:
                    continue
        finally:
            try:
                out.unlink()
                tf.unlink()
            except Exception:
                pass
    # synthesize finding records
    structured = [{
        "template": f.get("template-id") or f.get("templateID"),
        "severity": (f.get("info") or {}).get("severity"),
        "name":     (f.get("info") or {}).get("name"),
        "host":     f.get("host") or f.get("url"),
        "matched":  f.get("matched-at") or f.get("matched"),
        "project_id": project_id,
    } for f in findings]
    return envelope("nuclei", result, findings=findings, structured=structured)
