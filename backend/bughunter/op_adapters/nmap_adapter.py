"""nmap adapter — port/service discovery with XML parsing into asset DB."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ._common import locate, run_cmd, envelope


def run(app, *, targets: list[str] | str, ports: str = "top-1000",
        scripts: list[str] | None = None, timing: str = "T3",
        extra_args: list[str] | None = None, timeout: int = 1800,
        project_id: str | None = None, **_) -> dict:
    """Run nmap and parse -oX output into hosts/services."""
    binary = locate("nmap", hint="brew install nmap")
    if isinstance(targets, str):
        targets_list = [t.strip() for t in targets.split() if t.strip()]
    else:
        targets_list = list(targets)
    if not targets_list:
        raise ValueError("nmap requires targets")

    xml_path = Path(app.store.root) / "workspaces" / f"nmap-{int(__import__('time').time()*1000)}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    argv = [binary]
    if ports == "top-1000":
        argv += ["--top-ports", "1000"]
    elif ports == "all":
        argv += ["-p-"]
    else:
        argv += ["-p", ports]
    argv += ["-sV", f"-{timing}", "-oX", str(xml_path)]
    for script in (scripts or []):
        argv += ["--script", script]
    argv += list(extra_args or [])
    argv += targets_list

    result = run_cmd(argv, timeout=timeout)
    hosts, services = _parse(xml_path) if xml_path.exists() else ([], [])
    for h in hosts:
        h["project_id"] = project_id
    return envelope("nmap", result, hosts=hosts, services=services,
                    xml_path=str(xml_path))


def _parse(xml_path: Path) -> tuple[list[dict], list[dict]]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return [], []
    hosts: list[dict] = []
    services: list[dict] = []
    for host_el in tree.getroot().findall("host"):
        state = host_el.find("status")
        if state is not None and state.get("state") != "up":
            continue
        ip = None
        hostname = None
        for addr in host_el.findall("address"):
            if addr.get("addrtype") in {"ipv4", "ipv6"}:
                ip = addr.get("addr")
        names = host_el.find("hostnames")
        if names is not None:
            for n in names.findall("hostname"):
                hostname = n.get("name")
                break
        os_hint = None
        os_el = host_el.find("os/osmatch")
        if os_el is not None:
            os_hint = os_el.get("name")
        host_key = ip or hostname
        if not host_key:
            continue
        hosts.append({"ip": ip, "hostname": hostname, "os_hint": os_hint})
        ports_el = host_el.find("ports")
        if ports_el is not None:
            for p in ports_el.findall("port"):
                ps = p.find("state")
                if ps is not None and ps.get("state") != "open":
                    continue
                svc = p.find("service")
                services.append({
                    "host_key": host_key,     # the ingestor resolves this to host_id
                    "port": int(p.get("portid", 0)),
                    "proto": p.get("protocol", "tcp"),
                    "name": svc.get("name") if svc is not None else None,
                    "banner": (svc.get("product", "") + " " +
                               svc.get("version", "")).strip() if svc is not None else None,
                })
    return hosts, services
