"""Local inference helper — serve the base model + active LoRA adapters.

On the 8 GB M1 daily driver, inference runs locally via Ollama (small
quantized base model). This service:

- Builds an Ollama ``Modelfile`` that composes the chosen base with the
  active adapters recorded in ``AdapterRegistry.active()``.
- Creates / refreshes the custom Ollama model so the existing
  ``provider: ollama`` path picks it up unchanged.
- Can optionally launch an ``mlx_lm.server`` if MLX-LM is installed and
  the operator wants that path instead.

Nothing here trains — training is the Kaggle side.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .store import Store, now


class LocalInference:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "inference.build_ollama":
            return self.build_ollama(**(params or {}))
        if method == "inference.status":
            return self.status()
        if method == "inference.mlx_serve":
            return self.mlx_serve(**(params or {}))
        if method == "inference.mlx_stop":
            return self.mlx_stop()
        raise ValueError(f"Unknown inference method: {method}")

    # ollama --------------------------------------------------------------

    def build_ollama(self, *, base_model: str,
                     custom_name: str = "bughunter-active",
                     system: str | None = None,
                     approved: bool = True, **_) -> dict:
        self.store.require_desktop_owner()
        ollama = shutil.which("ollama")
        if not ollama:
            raise RuntimeError("ollama not on PATH. brew install ollama")
        active = self.app.adapters_registry.active()
        # Ollama supports LoRA adapters via ADAPTER directive since 0.3.0.
        # If multiple adapters are active we only honor the first (Ollama
        # composition of adapters is single-slot). The registry picks order.
        adapter_line = ""
        if active:
            top = active[0]
            # Ollama expects a GGUF-compatible adapter. If the Kaggle output
            # is HF-format, the operator must convert with the llama.cpp
            # ``convert-lora-to-gguf.py`` tool first. We point at the folder
            # the operator chose.
            adapter_path = top.get("gguf_path") or top.get("path")
            adapter_line = f"ADAPTER {adapter_path}\n"
        system_block = f'SYSTEM """{system.strip()}"""\n' if system else ""
        modelfile = f"FROM {base_model}\n{adapter_line}{system_block}"
        mf_path = Path(self.store.root) / "workspaces" / "Modelfile.active"
        mf_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mf_path.write_text(modelfile)
        proc = subprocess.run([ollama, "create", custom_name,
                               "-f", str(mf_path)],
                              capture_output=True, text=True, timeout=120)
        record = {"custom_name": custom_name,
                  "base_model": base_model,
                  "adapters": [a["id"] for a in active],
                  "modelfile": str(mf_path),
                  "status": "ok" if proc.returncode == 0 else "error",
                  "stderr": proc.stderr[:4000],
                  "updated_at": now()}
        self.store.set_setting("local_inference_ollama", record)
        return record

    # mlx -----------------------------------------------------------------

    def mlx_serve(self, *, base_model: str, port: int = 11435,
                  approved: bool = True, **_) -> dict:
        """Start an MLX-LM server with the active adapter mounted."""
        self.store.require_desktop_owner()
        active = self.app.adapters_registry.active()
        argv = ["mlx_lm.server", "--model", base_model, "--port", str(port)]
        if active:
            argv += ["--adapter-path", active[0]["path"]]
        try:
            proc = subprocess.Popen(argv, start_new_session=True,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise RuntimeError("mlx_lm.server not found. "
                               "pip install mlx-lm")
        self.store.set_setting("local_inference_mlx",
                               {"pid": proc.pid, "port": port,
                                "base_model": base_model,
                                "adapters": [a["id"] for a in active],
                                "started_at": now()})
        return {"pid": proc.pid, "port": port}

    def mlx_stop(self, **_) -> dict:
        record = self.store.setting("local_inference_mlx", None)
        if not record:
            return {"stopped": None}
        import os, signal
        try:
            os.kill(record["pid"], signal.SIGTERM)
        except Exception:
            pass
        self.store.set_setting("local_inference_mlx", None)
        return {"stopped": record.get("pid")}

    def status(self, **_) -> dict:
        return {
            "ollama": self.store.setting("local_inference_ollama", None),
            "mlx":    self.store.setting("local_inference_mlx", None),
            "active_adapters": self.app.adapters_registry.active(),
        }
