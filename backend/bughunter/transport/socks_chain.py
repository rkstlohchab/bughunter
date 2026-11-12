"""SOCKS chain manager.

Lets the operator define an ordered list of SOCKS hops (upstream
proxies, Tor circuits via ``stem``, etc.) and expose a single local
SOCKS5 endpoint that the operator agent's adapters route through.

Underlying: we drive ``gost`` or ``redsocks`` when installed (both ship
chain support). If neither is present we expose the list as a chain spec
that the adapter wrappers can honor directly via per-tool ``--proxy``
flags.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..store import Store, new_id, now


class SocksChain:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self._proc: subprocess.Popen | None = None

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "socks.list":
            return self.list()
        if method == "socks.save":
            return self.save(**params)
        if method == "socks.delete":
            return self.delete(**params)
        if method == "socks.start":
            return self.start(**params)
        if method == "socks.stop":
            return self.stop()
        if method == "socks.status":
            return self.status()
        raise ValueError(f"Unknown socks method: {method}")

    # chain defs ----------------------------------------------------------

    def list(self, **_) -> list[dict]:
        return self.store.list("socks_chain")

    def save(self, *, name: str, hops: list[dict], id: str | None = None,
             **_) -> dict:
        self.store.require_desktop_owner()
        if not hops:
            raise ValueError("chain needs at least one hop")
        for hop in hops:
            if not {"type", "host", "port"} <= hop.keys():
                raise ValueError("each hop needs type/host/port")
            if hop["type"] not in {"socks5", "socks4", "tor", "http"}:
                raise ValueError(f"unknown hop type: {hop['type']}")
        chain_id = id or new_id("chain")
        record = {"id": chain_id, "name": name, "hops": hops,
                  "updated_at": now()}
        self.store.put("socks_chain", record, id=chain_id)
        return record

    def delete(self, *, id: str, **_) -> dict:
        self.store.require_desktop_owner()
        self.store.delete("socks_chain", id)
        return {"deleted": id}

    # runtime -------------------------------------------------------------

    def start(self, *, chain_id: str, local_port: int = 1080,
              approved: bool = True, **_) -> dict:
        self.store.require_desktop_owner()
        chain = self.store.get("socks_chain", chain_id)
        if not chain:
            raise ValueError("Unknown chain.")
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        gost = shutil.which("gost")
        if gost:
            argv = self._gost_argv(gost, chain["hops"], local_port)
            self._proc = subprocess.Popen(argv, start_new_session=True,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            backend = "gost"
        else:
            # record-only mode (adapters honor the chain spec themselves)
            self._proc = None
            backend = "spec-only"
        record = {"chain_id": chain_id, "local_port": local_port,
                  "backend": backend, "started_at": now()}
        self.store.set_setting("socks_chain_active", record)
        return record

    def stop(self, **_) -> dict:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        self._proc = None
        self.store.set_setting("socks_chain_active", None)
        return {"stopped": True}

    def status(self, **_) -> dict:
        active = self.store.setting("socks_chain_active", None)
        alive = bool(self._proc and self._proc.poll() is None)
        return {"active": active, "running": alive}

    # internals -----------------------------------------------------------

    def _gost_argv(self, gost: str, hops: list[dict], local_port: int) -> list[str]:
        argv = [gost, "-L", f"socks5://127.0.0.1:{local_port}"]
        for hop in hops:
            if hop["type"] == "tor":
                argv += ["-F", f"socks5://{hop['host']}:{hop['port']}"]
            elif hop["type"] in {"socks5", "socks4"}:
                argv += ["-F", f"{hop['type']}://{hop['host']}:{hop['port']}"]
            elif hop["type"] == "http":
                argv += ["-F", f"http://{hop['host']}:{hop['port']}"]
        return argv
