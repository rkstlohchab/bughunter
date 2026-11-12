"""httpx adapter — HTTP probe fleet with JSON output."""
from __future__ import annotations

import json
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, targets: list[str] | str,
        probes: list[str] | None = None,
        follow_redirects: bool = True,
        threads: int = 25, timeout: int = 10,
        extra_args: list[str] | None = None,
        max_duration: int = 600, project_id: str | None = None, **_) -> dict:
    binary = locate("httpx", hint="go install github.com/projectdiscovery/httpx/cmd/httpx@latest")
    if isinstance(targets, str):
        targets_list = [t.strip() for t in targets.splitlines() if t.strip()]
    else:
        targets_list = list(targets)
    if not targets_list:
        raise ValueError("httpx requires targets")

    import tempfile, pathlib
    tf = pathlib.Path(tempfile.mkstemp(prefix="httpx-", suffix=".txt")[1])
    tf.write_text("\n".join(targets_list))

    argv = [binary, "-l", str(tf), "-json", "-silent",
            "-threads", str(threads), "-timeout", str(timeout)]
    if follow_redirects:
        argv += ["-follow-redirects"]
    for probe in (probes or ["status-code", "title", "tech-detect",
                             "content-length", "web-server", "method"]):
        argv += [f"-{probe}"]
    argv += list(extra_args or [])

    result = run_cmd(argv, timeout=max_duration)
    try:
        tf.unlink()
    except Exception:
        pass
    findings = _parse(result["stdout"])
    # extract synthetic hosts
    hosts = []
    for f in findings:
        host = f.get("host") or f.get("input")
        if host:
            hosts.append({"hostname": host, "project_id": project_id})
    return envelope("httpx", result, findings=findings, hosts=hosts)


def _parse(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
