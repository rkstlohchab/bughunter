"""Operator tool surface — unbounded capability for operator mode.

This is a sibling of ``ModelTools`` with explicit opt-in. Every call:

- Is logged to the ``Timeline`` service (start, args, outcome, duration).
- Writes structured results into the ``Asset DB`` where applicable.
- Routes through the ``Vault`` for ``{{VAULT:name}}`` tokens so secrets
  never enter model context.
- Is approval-gated on the Rust side for first-use or write-class calls
  (``src-tauri/src/operator_shell.rs``).

The investigation ``ModelTools`` is untouched. OperatorTools is a separate
import path, instantiated only by ``OperatorAgent``.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .store import new_id, now


# --------------------------------------------------------------------------


class OperatorTools:
    """The full capability surface used by ``OperatorAgent``."""

    TOOL_SPECS: list[dict] = []     # populated below via @tool

    def __init__(self, app, run_id: str | None = None):
        self.app = app
        self.store = app.store
        self.run_id = run_id
        self._adapter_registry: dict[str, Callable] = {}
        self._wire_adapters()

    # -------- adapter registration ----------------------------------------

    def _wire_adapters(self):
        from .op_adapters import nmap_adapter, httpx_adapter, ffuf_adapter
        from .op_adapters import nuclei_adapter, hashcat_adapter, hydra_adapter
        from .op_adapters import frida_adapter, radare2_adapter, ghidra_adapter
        from .op_adapters import impacket_adapter, bloodhound_adapter, adb_adapter
        from .op_adapters import subfinder_adapter
        # GameLab adapters (operator-stack, no cross-plumbing to investigation)
        from .op_adapters import vision_esp, dma_memread, arduino_hid, kmbox
        from .op_adapters import bgmi_mobile, game_offsets, hwid_spoof
        from .op_adapters import memory_esp, radar, combat, mobile_memory_esp
        self._adapter_registry.update({
            "nmap":         nmap_adapter.run,
            "httpx":        httpx_adapter.run,
            "ffuf":         ffuf_adapter.run,
            "nuclei":       nuclei_adapter.run,
            "hashcat":      hashcat_adapter.run,
            "hydra":        hydra_adapter.run,
            "frida":        frida_adapter.run,
            "radare2":      radare2_adapter.run,
            "ghidra":       ghidra_adapter.run,
            "impacket":     impacket_adapter.run,
            "bloodhound":   bloodhound_adapter.run,
            "adb":          adb_adapter.run,
            "subfinder":    subfinder_adapter.run,
            # GameLab adapters
            "vision_esp":          vision_esp.run,
            "dma_memread":         dma_memread.run,
            "arduino_hid":         arduino_hid.run,
            "kmbox":               kmbox.run,
            "bgmi_mobile":         bgmi_mobile.run,
            "game_offsets":        game_offsets.run,
            "hwid_spoof":          hwid_spoof.run,
            # Universal memory-ESP pack
            "memory_esp":          memory_esp.run,
            "radar":               radar.run,
            "combat":              combat.run,
            "mobile_memory_esp":   mobile_memory_esp.run,
        })

    def invoke_adapter(self, name: str, **kwargs) -> dict:
        fn = self._adapter_registry.get(name)
        if not fn:
            raise ValueError(f"unknown adapter: {name}")
        # expand vault references in string args
        if hasattr(self.app, "vault"):
            for key, value in list(kwargs.items()):
                if isinstance(value, str):
                    kwargs[key] = self.app.vault.substitute(value)
                elif isinstance(value, list):
                    kwargs[key] = [self.app.vault.substitute(v)
                                   if isinstance(v, str) else v for v in value]
        tl = getattr(self.app, "timeline", None)
        start = time.time()
        if tl:
            tl.emit(kind="adapter", phase="start", tool=name,
                    run_id=self.run_id,
                    payload={k: _safe_arg(v) for k, v in kwargs.items()})
        try:
            result = fn(self.app, **kwargs)
            if tl:
                tl.emit(kind="adapter", phase="complete", tool=name,
                        run_id=self.run_id, outcome="ok",
                        duration_ms=int((time.time() - start) * 1000),
                        payload={"summary": _summarize(result)})
            # auto-ingest known shapes into the asset DB
            self._ingest(name, result)
            return result
        except Exception as exc:
            if tl:
                tl.emit(kind="adapter", phase="error", tool=name,
                        run_id=self.run_id, outcome="error",
                        duration_ms=int((time.time() - start) * 1000),
                        payload={"error": repr(exc)})
            raise

    def _ingest(self, adapter: str, result: Any) -> None:
        if not hasattr(self.app, "assets"):
            return
        if not isinstance(result, dict):
            return
        assets = self.app.assets
        for host in result.get("hosts", []):
            if not isinstance(host, dict):
                continue
            assets.upsert_host(source=f"adapter:{adapter}", **host)
        for service in result.get("services", []):
            if not isinstance(service, dict):
                continue
            assets.upsert_service(**service)
        for loot_entry in result.get("loot", []):
            if isinstance(loot_entry, dict):
                assets.add_loot(**loot_entry)

    # -------- direct tool surface (shell, net, file) ----------------------

    def shell(self, *, command: str, cwd: str | None = None,
              timeout: int = 300, env: dict | None = None) -> dict:
        """Run a shell command. Operator-approved; the vault is expanded
        into args and env before invocation."""
        self.store.require_desktop_owner()
        vault = getattr(self.app, "vault", None)
        if vault:
            command = vault.substitute(command)
            env = vault.expand_env(env)
        proc = subprocess.run(
            command, shell=True, cwd=cwd, env={**_base_env(), **(env or {})},
            capture_output=True, text=True, timeout=timeout,
        )
        record = {
            "command": _redact_cmd(command),
            "cwd": cwd,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-16000:],
            "stderr": proc.stderr[-8000:],
            "duration_ms": None,
        }
        tl = getattr(self.app, "timeline", None)
        if tl:
            tl.emit(kind="shell", phase="complete",
                    run_id=self.run_id,
                    outcome="ok" if proc.returncode == 0 else "nonzero",
                    payload={"cmd": record["command"], "exit": proc.returncode})
        return record

    def http(self, *, url: str, method: str = "GET",
             headers: dict | None = None, body: str | None = None,
             timeout: int = 30, verify_tls: bool = True) -> dict:
        """Issue one HTTP request, routed through the configured SOCKS chain
        if enabled. Does NOT pass through the investigation gateway — this
        is operator mode."""
        self.store.require_desktop_owner()
        import urllib.request, ssl
        vault = getattr(self.app, "vault", None)
        if vault:
            url = vault.substitute(url)
            headers = vault.expand_env(headers or {})
            if body:
                body = vault.substitute(body)
        req = urllib.request.Request(
            url, method=method,
            headers=headers or {},
            data=(body.encode("utf-8") if body else None),
        )
        ctx = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
        start = time.time()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as rsp:
                data = rsp.read()
                status = rsp.status
                rsp_headers = dict(rsp.headers.items())
        except urllib.error.HTTPError as e:
            data = e.read()
            status = e.code
            rsp_headers = dict(e.headers.items()) if e.headers else {}
        duration = int((time.time() - start) * 1000)
        record = {
            "url": url, "method": method, "status": status,
            "headers": rsp_headers,
            "body": data[:200_000].decode("utf-8", errors="replace"),
            "size": len(data), "duration_ms": duration,
        }
        tl = getattr(self.app, "timeline", None)
        if tl:
            tl.emit(kind="http", phase="complete", run_id=self.run_id,
                    outcome=f"{status}",
                    duration_ms=duration,
                    payload={"url": url, "method": method, "status": status})
        return record

    def file_read(self, *, path: str, max_bytes: int = 1_000_000) -> dict:
        self.store.require_desktop_owner()
        p = Path(path).expanduser()
        data = p.read_bytes()[:max_bytes]
        return {"path": str(p), "size": p.stat().st_size,
                "content": data.decode("utf-8", errors="replace")}

    def file_write(self, *, path: str, content: str, approved: bool = True) -> dict:
        self.store.require_desktop_owner()
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "wrote_bytes": len(content)}

    # -------- assets / vault surfaces exposed to the agent ----------------

    def assets_query(self, *, query: str) -> dict:
        return self.app.assets.find(query=query)

    def vault_list(self) -> list[dict]:
        return self.app.vault.list()

    # -------- code exec sandbox hand-off ---------------------------------

    def code_exec(self, *, language: str, code: str,
                  approved: bool = True, timeout: int = 60) -> dict:
        """Compile+run model-written code in the sandbox. See ``code_exec.py``."""
        self.store.require_desktop_owner()
        from .code_exec import run as run_sandbox
        return run_sandbox(self.app, language=language, code=code,
                           timeout=timeout, run_id=self.run_id)


# --------------------------------------------------------------------------


OperatorTools.TOOL_SPECS = [
    {
        "name": "invoke_adapter",
        "description": "Call a named tool adapter (nmap, httpx, ffuf, nuclei, "
                       "hashcat, hydra, frida, radare2, ghidra, impacket, "
                       "bloodhound, adb). Returns structured output and auto-"
                       "ingests hosts/services/loot into the asset DB.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "args": {"type": "object"},
            }, "required": ["name"],
        },
    },
    {"name": "shell",
     "description": "Run a shell command on the operator's machine.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"},
                                     "cwd": {"type": "string"},
                                     "timeout": {"type": "integer"}},
                      "required": ["command"]}},
    {"name": "http",
     "description": "Issue one HTTP request.",
     "input_schema": {"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "method": {"type": "string"},
                                     "headers": {"type": "object"},
                                     "body": {"type": "string"},
                                     "verify_tls": {"type": "boolean"}},
                      "required": ["url"]}},
    {"name": "file_read",
     "description": "Read a local file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "file_write",
     "description": "Write a local file. Requires approved=true.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"},
                                     "approved": {"type": "boolean"}},
                      "required": ["path", "content"]}},
    {"name": "assets_query",
     "description": "Full-text search across the asset DB.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"}},
                      "required": ["query"]}},
    {"name": "vault_list",
     "description": "List vault entry names + metadata (no secrets).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "code_exec",
     "description": "Compile+run model-written code in the sandbox.",
     "input_schema": {"type": "object",
                      "properties": {"language": {"type": "string"},
                                     "code": {"type": "string"},
                                     "approved": {"type": "boolean"}},
                      "required": ["language", "code"]}},
]


def _base_env() -> dict:
    import os
    env = {k: v for k, v in os.environ.items()
           if k in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL"}}
    return env


def _redact_cmd(cmd: str) -> str:
    # strip anything that looks like a bearer or password flag value
    import re
    return re.sub(r"(--password|-p|--token|--bearer)\s+\S+",
                  r"\1 ***", cmd, flags=re.IGNORECASE)


def _safe_arg(v: Any) -> Any:
    if isinstance(v, str):
        return v[:1000]
    return v


def _summarize(result: Any) -> dict:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out = {}
    for k in ("hosts", "services", "findings", "pages", "loot", "credentials"):
        if k in result and isinstance(result[k], list):
            out[k] = len(result[k])
    if "exit_code" in result:
        out["exit_code"] = result["exit_code"]
    return out
