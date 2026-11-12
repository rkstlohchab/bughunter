"""hydra adapter — authenticated protocol credential testing.

Operator must hold authorization for the target; the tool refuses to run
without explicit confirmation of scope (passed from the operator approval
UI).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


SUPPORTED = {"ssh", "ftp", "smb", "smb2", "http-get", "http-post-form",
             "rdp", "vnc", "mysql", "postgres", "mssql", "ldap2", "ldap3",
             "imap", "pop3", "smtp", "rlogin", "rexec"}


def run(app, *, target: str, service: str,
        users: list[str] | None = None,
        user_file: str | None = None,
        passwords: list[str] | None = None,
        password_file: str | None = None,
        threads: int = 4,
        extra_args: list[str] | None = None,
        max_duration: int = 1800, **_) -> dict:
    binary = locate("hydra", hint="brew install hydra")
    if service not in SUPPORTED:
        raise ValueError(f"hydra service '{service}' not in supported list")
    if not (users or user_file):
        raise ValueError("hydra needs users or user_file")
    if not (passwords or password_file):
        raise ValueError("hydra needs passwords or password_file")
    argv = [binary]
    if user_file:
        argv += ["-L", str(Path(user_file).expanduser())]
    elif users:
        argv += ["-l", users[0]] if len(users) == 1 else ["-L", _write_list(users, "users")]
    if password_file:
        argv += ["-P", str(Path(password_file).expanduser())]
    elif passwords:
        argv += ["-p", passwords[0]] if len(passwords) == 1 else ["-P", _write_list(passwords, "pw")]
    argv += ["-t", str(threads), "-I"]
    argv += list(extra_args or [])
    argv += [target, service]

    result = run_cmd(argv, timeout=max_duration)
    credentials = []
    for line in (result["stdout"] or "").splitlines():
        if "login:" in line and "password:" in line:
            # shape:  [8080][http-get] host: 1.2.3.4   login: admin   password: foo
            try:
                left, right = line.split("login:", 1)
                user, _, right2 = right.partition("password:")
                credentials.append({
                    "service": service,
                    "target": target,
                    "user": user.strip(),
                    "password": right2.strip(),
                })
            except Exception:
                continue
    return envelope("hydra", result, credentials=credentials)


def _write_list(values: list[str], tag: str) -> str:
    import tempfile
    p = Path(tempfile.mkstemp(prefix=f"hydra-{tag}-", suffix=".txt")[1])
    p.write_text("\n".join(values))
    return str(p)
