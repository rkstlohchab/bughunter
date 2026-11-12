"""subfinder adapter — passive subdomain discovery.

Operator-mode counterpart to the existing bounded ``ToolService.subfinder``.
Returns a structured list of subdomains for workflow chaining.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, domain: str, timeout: int = 600,
        sources: list[str] | None = None,
        resolvers: list[str] | None = None,
        extra_args: list[str] | None = None, **_) -> dict:
    binary = locate("subfinder",
                    hint="go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest")
    out = Path(tempfile.mkstemp(prefix="subfinder-", suffix=".txt")[1])
    argv = [binary, "-d", domain, "-o", str(out), "-silent"]
    if sources:
        argv += ["-sources", ",".join(sources)]
    if resolvers:
        argv += ["-rL"] + resolvers
    argv += list(extra_args or [])
    result = run_cmd(argv, timeout=timeout)
    subdomains = []
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                subdomains.append(line.strip())
        try:
            out.unlink()
        except Exception:
            pass
    hosts = [{"hostname": s} for s in subdomains]
    return envelope("subfinder", result, subdomains=subdomains,
                    hostnames=subdomains, hosts=hosts)
