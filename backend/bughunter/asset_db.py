"""Asset DB — host / service / credential / loot / relation inventory.

Stores structured results from tool adapters across sessions so runs
compound. Lives alongside the existing ``project`` scope but is not
gated by it — assets are operator-scoped and visible across projects
the operator owns.

Entity kinds introduced here:
- ``host``       : ``{id, project_id, ip, hostname, os_hint, source, last_seen}``
- ``service``    : ``{id, host_id, port, proto, name, banner, fingerprint}``
- ``credential`` : ``{id, host_id?, service_id?, type, user, secret_ref, source}``
                   secret_ref points at a vault entry; raw secrets never live here
- ``loot``       : ``{id, host_id?, service_id?, kind, path, sha256, notes}``
- ``relation``   : ``{id, src, dst, kind, evidence_ref, notes}``
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any

from .store import Store, new_id, now


class AssetDB:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store

    def dispatch(self, method: str, params: dict) -> Any:
        verbs = {
            "assets.hosts": self.list_hosts,
            "assets.services": self.list_services,
            "assets.credentials": self.list_credentials,
            "assets.loot": self.list_loot,
            "assets.relations": self.list_relations,
            "assets.find": self.find,
            "assets.upsert_host": self.upsert_host,
            "assets.upsert_service": self.upsert_service,
            "assets.add_credential": self.add_credential,
            "assets.add_loot": self.add_loot,
            "assets.link": self.link,
            "assets.delete": self.delete,
            "assets.graph": self.graph,
        }
        fn = verbs.get(method)
        if not fn:
            raise ValueError(f"Unknown assets method: {method}")
        return fn(**(params or {}))

    # upserts -------------------------------------------------------------

    def upsert_host(self, *, ip: str | None = None, hostname: str | None = None,
                    project_id: str | None = None, os_hint: str | None = None,
                    source: str | None = None, **_: Any) -> dict:
        if not (ip or hostname):
            raise ValueError("host requires ip or hostname")
        if ip:
            try:
                ipaddress.ip_address(ip)
            except ValueError as e:
                raise ValueError(f"invalid ip: {e}") from e
        existing = self._find_host(ip=ip, hostname=hostname,
                                   project_id=project_id)
        host_id = existing["id"] if existing else new_id("host")
        record = {
            "id": host_id,
            "project_id": project_id,
            "ip": ip, "hostname": hostname,
            "os_hint": os_hint or (existing or {}).get("os_hint"),
            "source": source or (existing or {}).get("source"),
            "first_seen": (existing or {}).get("first_seen", now()),
            "last_seen": now(),
        }
        self.store.put("host", record, id=host_id)
        return record

    def upsert_service(self, *, host_id: str, port: int, proto: str = "tcp",
                       name: str | None = None, banner: str | None = None,
                       fingerprint: str | None = None, **_: Any) -> dict:
        if not host_id:
            raise ValueError("service requires host_id")
        existing = self._find_service(host_id, port, proto)
        service_id = existing["id"] if existing else new_id("service")
        record = {
            "id": service_id,
            "host_id": host_id,
            "port": int(port),
            "proto": proto,
            "name": name or (existing or {}).get("name"),
            "banner": (banner or (existing or {}).get("banner") or "")[:2000],
            "fingerprint": fingerprint or (existing or {}).get("fingerprint"),
            "first_seen": (existing or {}).get("first_seen", now()),
            "last_seen": now(),
        }
        self.store.put("service", record, id=service_id)
        return record

    def add_credential(self, *, type: str, user: str | None = None,
                       secret_ref: str | None = None,
                       host_id: str | None = None,
                       service_id: str | None = None,
                       source: str | None = None, **_: Any) -> dict:
        cred_id = new_id("cred")
        record = {
            "id": cred_id, "type": type, "user": user,
            "secret_ref": secret_ref,
            "host_id": host_id, "service_id": service_id,
            "source": source, "created_at": now(),
        }
        self.store.put("credential", record, id=cred_id)
        return record

    def add_loot(self, *, kind: str, path: str,
                 host_id: str | None = None, service_id: str | None = None,
                 notes: str | None = None, **_: Any) -> dict:
        p = Path(path)
        sha = ""
        if p.exists() and p.is_file():
            sha = _sha256(p)
        loot_id = new_id("loot")
        record = {
            "id": loot_id, "kind": kind, "path": str(p),
            "sha256": sha, "host_id": host_id, "service_id": service_id,
            "notes": notes, "created_at": now(),
        }
        self.store.put("loot", record, id=loot_id)
        return record

    def link(self, *, src: str, dst: str, kind: str,
             evidence_ref: str | None = None, notes: str | None = None,
             **_: Any) -> dict:
        rel_id = new_id("rel")
        record = {
            "id": rel_id, "src": src, "dst": dst, "kind": kind,
            "evidence_ref": evidence_ref, "notes": notes,
            "created_at": now(),
        }
        self.store.put("relation", record, id=rel_id)
        return record

    def delete(self, *, kind: str, id: str, **_: Any) -> dict:
        if kind not in {"host", "service", "credential", "loot", "relation"}:
            raise ValueError(f"not an asset kind: {kind}")
        self.store.delete(kind, id)
        return {"deleted": id}

    # queries -------------------------------------------------------------

    def list_hosts(self, *, project_id: str | None = None, **_) -> list[dict]:
        hosts = self.store.list("host")
        if project_id:
            hosts = [h for h in hosts if h.get("project_id") == project_id]
        return hosts

    def list_services(self, *, host_id: str | None = None, **_) -> list[dict]:
        services = self.store.list("service")
        if host_id:
            services = [s for s in services if s.get("host_id") == host_id]
        return services

    def list_credentials(self, *, host_id: str | None = None, **_) -> list[dict]:
        creds = self.store.list("credential")
        if host_id:
            creds = [c for c in creds if c.get("host_id") == host_id]
        return creds

    def list_loot(self, *, host_id: str | None = None, **_) -> list[dict]:
        loot = self.store.list("loot")
        if host_id:
            loot = [l for l in loot if l.get("host_id") == host_id]
        return loot

    def list_relations(self, *, host_id: str | None = None,
                       service_id: str | None = None, **_) -> list[dict]:
        relations = self.store.list("relation")
        target = host_id or service_id
        if target:
            relations = [r for r in relations
                         if r.get("src") == target or r.get("dst") == target]
        return relations

    def find(self, *, query: str, **_) -> dict:
        """Full search across assets."""
        needle = (query or "").lower()
        if not needle:
            return {"hosts": [], "services": [], "credentials": [], "loot": []}
        def _match(record: dict) -> bool:
            blob = json.dumps(record, default=str).lower()
            return needle in blob
        return {
            "hosts":       [h for h in self.store.list("host")       if _match(h)],
            "services":    [s for s in self.store.list("service")    if _match(s)],
            "credentials": [c for c in self.store.list("credential") if _match(c)],
            "loot":        [l for l in self.store.list("loot")       if _match(l)],
        }

    def graph(self, *, project_id: str | None = None, **_) -> dict:
        hosts = self.list_hosts(project_id=project_id)
        host_ids = {h["id"] for h in hosts}
        services = [s for s in self.store.list("service")
                    if s.get("host_id") in host_ids]
        relations = [r for r in self.store.list("relation")
                     if r.get("src") in host_ids or r.get("dst") in host_ids]
        return {"hosts": hosts, "services": services, "relations": relations}

    # internals -----------------------------------------------------------

    def _find_host(self, *, ip: str | None, hostname: str | None,
                   project_id: str | None) -> dict | None:
        for h in self.store.list("host"):
            if project_id and h.get("project_id") != project_id:
                continue
            if ip and h.get("ip") == ip:
                return h
            if hostname and h.get("hostname") == hostname:
                return h
        return None

    def _find_service(self, host_id: str, port: int, proto: str) -> dict | None:
        for s in self.store.list("service"):
            if (s.get("host_id") == host_id and int(s.get("port", 0)) == int(port)
                    and s.get("proto") == proto):
                return s
        return None


def _sha256(path: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
