"""Adapter registry — list, activate, compose LoRA adapters.

An adapter is a trained delta (LoRA) for a domain: web-audit, binary-re,
mobile, network, memory, osint, codebase, dj-voice, or any other operator
label. The registry tracks them on disk, records provenance (which corpus
they came from), and exposes the composition selected for a run.

Serving side (Ollama / MLX-LM / llama.cpp) is wired in ``local_inference.py``
and merges the selected set at inference time. The registry only owns
metadata and the on-disk paths.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .store import Store, new_id, now


class AdapterRegistry:
    """Store + dispatch surface for trained LoRA adapters."""

    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self.root = Path(self.store.root) / "adapters"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # dispatch ------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "adapters.list":
            return self.list()
        if method == "adapters.get":
            return self.get(params["id"])
        if method == "adapters.register":
            return self.register(**params)
        if method == "adapters.activate":
            return self.set_active(params.get("ids") or [])
        if method == "adapters.active":
            return self.active()
        if method == "adapters.delete":
            return self.delete(params["id"])
        if method == "adapters.import":
            return self.import_from(**params)
        raise ValueError(f"Unknown adapters method: {method}")

    # core ----------------------------------------------------------------

    def list(self) -> list[dict]:
        return self.store.list("adapter")

    def get(self, adapter_id: str) -> dict | None:
        return self.store.get("adapter", adapter_id)

    def register(self, *, name: str, domain: str, base_model: str,
                 path: str, corpus_id: str | None = None,
                 kaggle_run: str | None = None, rank: int | None = None,
                 alpha: int | None = None, notes: str = "") -> dict:
        """Register a trained adapter already present on disk."""
        self.store.require_desktop_owner()
        p = Path(path).resolve()
        if not p.exists():
            raise ValueError(f"Adapter path does not exist: {p}")
        if not any(p.rglob("*.safetensors")) and not any(p.rglob("adapter_model.*")):
            raise ValueError("Adapter folder does not look like a LoRA checkpoint "
                             "(no safetensors/adapter_model.*).")
        adapter_id = new_id("adapter")
        # move/copy into managed location
        dst = self.root / f"{adapter_id}-{_slug(name)}"
        if p != dst:
            shutil.copytree(p, dst, dirs_exist_ok=False)
        record = {
            "id": adapter_id,
            "name": name,
            "domain": domain,
            "base_model": base_model,
            "path": str(dst),
            "corpus_id": corpus_id,
            "kaggle_run": kaggle_run,
            "rank": rank,
            "alpha": alpha,
            "notes": notes,
            "status": "staged",
            "fingerprint": _dir_fingerprint(dst),
            "created_at": now(),
        }
        self.store.put("adapter", record, id=adapter_id)
        self.store.event("adapter.registered",
                         {"id": adapter_id, "domain": domain, "base": base_model})
        return record

    def set_active(self, ids: list[str]) -> dict:
        """Mark a composition active for the operator. Serving picks it up."""
        self.store.require_desktop_owner()
        # verify each
        chosen = []
        for aid in ids:
            rec = self.store.get("adapter", aid)
            if not rec:
                raise ValueError(f"Unknown adapter: {aid}")
            chosen.append(rec)
        self.store.set_setting("active_adapters", [c["id"] for c in chosen])
        self.store.event("adapter.activate", {"ids": [c["id"] for c in chosen]})
        return {"active": chosen}

    def active(self) -> list[dict]:
        ids = self.store.setting("active_adapters", []) or []
        return [a for a in (self.store.get("adapter", i) for i in ids) if a]

    def delete(self, adapter_id: str) -> dict:
        self.store.require_desktop_owner()
        rec = self.store.get("adapter", adapter_id)
        if not rec:
            raise ValueError("Unknown adapter.")
        shutil.rmtree(rec["path"], ignore_errors=True)
        self.store.delete("adapter", adapter_id)
        # also remove from active if present
        active = self.store.setting("active_adapters", []) or []
        if adapter_id in active:
            active.remove(adapter_id)
            self.store.set_setting("active_adapters", active)
        self.store.event("adapter.deleted", {"id": adapter_id})
        return {"deleted": adapter_id}

    def import_from(self, *, archive: str, name: str, domain: str,
                    base_model: str, corpus_id: str | None = None,
                    kaggle_run: str | None = None) -> dict:
        """Import an adapter that was trained externally (Kaggle) and
        delivered as a zip of safetensors + adapter_config.json.
        """
        import zipfile
        src = Path(archive).resolve()
        if not src.exists():
            raise ValueError(f"Archive not found: {src}")
        stage = self.root / f"_stage_{new_id('x')}"
        stage.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            with zipfile.ZipFile(src) as z:
                z.extractall(stage)
            # if the zip contained a single top folder, unwrap it
            entries = [e for e in stage.iterdir()]
            if len(entries) == 1 and entries[0].is_dir():
                real_root = entries[0]
            else:
                real_root = stage
            rec = self.register(
                name=name, domain=domain, base_model=base_model,
                path=str(real_root), corpus_id=corpus_id, kaggle_run=kaggle_run,
                notes=f"imported from {src.name}",
            )
            return rec
        finally:
            shutil.rmtree(stage, ignore_errors=True)


# --------------------------------------------------------------------------


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-") or "adapter"


def _dir_fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(b"\x1f")
            h.update(p.stat().st_size.to_bytes(8, "little"))
            h.update(b"\x1f")
    return h.hexdigest()
