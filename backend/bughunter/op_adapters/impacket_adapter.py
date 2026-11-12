"""impacket adapter — AD / SMB / Kerberos tooling.

Supports the most-used scripts in the impacket suite. The tool list is
whitelisted because arbitrary impacket binary names are not safe to
template through the operator agent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


ALLOWED = {
    "secretsdump.py", "GetUserSPNs.py", "GetNPUsers.py", "GetADUsers.py",
    "smbclient.py", "smbexec.py", "psexec.py", "wmiexec.py", "atexec.py",
    "lookupsid.py", "rpcdump.py", "samrdump.py", "ticketer.py",
    "goldenPac.py", "mssqlclient.py", "mssqlinstance.py",
    "ntlmrelayx.py", "mqtt_check.py",
    "addcomputer.py", "dacledit.py", "describeTicket.py", "getST.py",
    "getTGT.py", "getPac.py", "kintercept.py", "machine_role.py",
    "mimikatz.py", "net.py", "netview.py", "owneredit.py", "ping.py",
    "ping6.py", "psexec.py", "raiseChild.py", "rbcd.py", "reg.py",
    "sambaPipe.py", "secretsdump.py", "services.py", "smbpasswd.py",
    "ticketConverter.py",
}


def run(app, *, tool: str, args: list[str] | None = None,
        extra_args: list[str] | None = None,
        max_duration: int = 1200, **_) -> dict:
    if tool not in ALLOWED:
        raise ValueError(f"impacket tool not allowed: {tool}")
    binary = locate(tool, hint="pip install impacket")
    argv = [binary] + list(args or []) + list(extra_args or [])
    return envelope("impacket", run_cmd(argv, timeout=max_duration),
                    impacket_tool=tool)
