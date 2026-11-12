"""Credential vault — Keychain-backed, operator-only.

Secrets live in the OS keychain (macOS Keychain on the daily driver).
The entity we store in the app DB is a **reference** (``vault_entry``)
that holds a name + metadata; the actual value is fetched by name from
the keychain at call time and injected into adapter commands through
template substitution (``{{VAULT:name}}``).

The value never enters:
- model context / transcripts
- the ``events`` table
- any file we write to disk outside the keychain

When an operator approves a tool call that uses a vault template, the
substitution happens at the subprocess layer; stdin/env vars carry the
secret and nothing writes it into the log.
"""
from __future__ import annotations

import re
from typing import Any

from .store import Store, new_id, now


VAULT_RE = re.compile(r"\{\{VAULT:([A-Za-z0-9._\-]+)\}\}")


class Vault:
    SERVICE = "BugHunterClaudex.vault"

    def __init__(self, app):
        self.app = app
        self.store: Store = app.store

    # dispatch ------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        verbs = {
            "vault.list": self.list,
            "vault.put": self.put,
            "vault.get_meta": self.get_meta,
            "vault.delete": self.delete,
            "vault.test": self.test,
            "vault.refs_in": self.refs_in,
        }
        fn = verbs.get(method)
        if not fn:
            raise ValueError(f"Unknown vault method: {method}")
        return fn(**(params or {}))

    # operations ----------------------------------------------------------

    def put(self, *, name: str, secret: str, kind: str = "token",
            notes: str | None = None, **_: Any) -> dict:
        self.store.require_desktop_owner()
        if not _valid_name(name):
            raise ValueError("Vault name must be alphanum + . _ -")
        if not secret:
            raise ValueError("Empty secret.")
        from .providers import keyring
        try:
            keyring.set_password(self.SERVICE, name, secret)
        except Exception as exc:
            raise ValueError(f"Keychain write failed: {exc}") from exc
        existing = self._by_name(name)
        entry_id = existing["id"] if existing else new_id("vault")
        record = {
            "id": entry_id,
            "name": name,
            "kind": kind,
            "notes": notes or (existing or {}).get("notes"),
            "created_at": (existing or {}).get("created_at", now()),
            "updated_at": now(),
        }
        self.store.put("vault_entry", record, id=entry_id)
        # Never persist or log the secret.
        return {**record, "stored": True}

    def get_meta(self, *, name: str, **_: Any) -> dict | None:
        return self._by_name(name)

    def list(self, **_: Any) -> list[dict]:
        return self.store.list("vault_entry")

    def delete(self, *, name: str, **_: Any) -> dict:
        self.store.require_desktop_owner()
        record = self._by_name(name)
        if not record:
            return {"deleted": None}
        from .providers import keyring
        try:
            keyring.delete_password(self.SERVICE, name)
        except Exception:
            pass
        self.store.delete("vault_entry", record["id"])
        return {"deleted": name}

    def test(self, *, name: str, **_: Any) -> dict:
        self.store.require_desktop_owner()
        value = self._load(name)
        return {"name": name, "present": bool(value), "length": len(value or "")}

    def refs_in(self, *, text: str, **_: Any) -> list[str]:
        return list(dict.fromkeys(VAULT_RE.findall(text or "")))

    # substitution surface (callers) --------------------------------------

    def substitute(self, text: str) -> str:
        """Expand ``{{VAULT:name}}`` tokens. Caller is responsible for the
        resulting text never being logged."""
        def repl(m):
            value = self._load(m.group(1))
            if not value:
                raise ValueError(f"Vault entry missing: {m.group(1)}")
            return value
        return VAULT_RE.sub(repl, text or "")

    def substitute_args(self, args: list[str]) -> list[str]:
        return [self.substitute(a) for a in args]

    def expand_env(self, env: dict | None) -> dict | None:
        if not env:
            return env
        return {k: self.substitute(v) for k, v in env.items()}

    # internals -----------------------------------------------------------

    def _by_name(self, name: str) -> dict | None:
        for e in self.store.list("vault_entry"):
            if e.get("name") == name:
                return e
        return None

    def _load(self, name: str) -> str:
        if not _valid_name(name):
            raise ValueError("Invalid vault name.")
        from .providers import keyring
        try:
            return keyring.get_password(self.SERVICE, name) or ""
        except Exception:
            return ""


def _valid_name(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in {".", "_", "-"} for c in s)
