"""Kaggle fine-tune bridge.

The 8 GB M1 Mac cannot host LoRA training. This service:

1. Packs a corpus bundle (produced by ``learning_forge.py``) + a launcher
   notebook + config into a Kaggle dataset.
2. Uses the Kaggle API (kaggle CLI, already an optional dep via
   ``kaggle_launcher.py``) to upload the dataset and create/update a notebook
   that runs the training.
3. Waits (or polls) for the notebook to finish, downloads the resulting
   adapter zip, and hands it off to ``AdapterRegistry.import_from``.

Everything that touches the network lives behind an operator-approved
dispatch call. Keys go through Keychain (via ``keyring``), never into
transcripts or events.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import Store, new_id, now


# --------------------------------------------------------------------------


@dataclass
class KaggleConfig:
    username: str
    api_key: str
    notebook_slug: str          # e.g. "dj/bughunter-forge-trainer"
    dataset_slug: str           # e.g. "dj/bughunter-corpus"
    output_dataset_slug: str    # e.g. "dj/bughunter-adapters"


class KaggleFinetune:
    """Dispatch surface: ``kaggle.*``."""

    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self.root = Path(self.store.root) / "kaggle"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # dispatch ------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "kaggle.configure":
            return self.configure(**params)
        if method == "kaggle.config":
            return self.config(redact=True)
        if method == "kaggle.push_corpus":
            return self.push_corpus(**params)
        if method == "kaggle.launch_training":
            return self.launch_training(**params)
        if method == "kaggle.status":
            return self.status(**params)
        if method == "kaggle.pull_adapter":
            return self.pull_adapter(**params)
        if method == "kaggle.list_runs":
            return self.list_runs()
        raise ValueError(f"Unknown kaggle method: {method}")

    # configuration -------------------------------------------------------

    def configure(self, *, username: str, api_key: str, notebook_slug: str,
                  dataset_slug: str, output_dataset_slug: str) -> dict:
        self.store.require_desktop_owner()
        from .providers import keyring  # reuse the same backend
        try:
            keyring.set_password("BugHunterClaudex", "kaggle_api_key", api_key)
        except Exception as exc:
            raise ValueError(f"Could not store Kaggle API key in Keychain: {exc}")
        cfg = {
            "username": username,
            "notebook_slug": notebook_slug,
            "dataset_slug": dataset_slug,
            "output_dataset_slug": output_dataset_slug,
            "updated_at": now(),
        }
        self.store.set_setting("kaggle_config", cfg)
        return {"ok": True, **cfg}

    def config(self, *, redact: bool = True) -> dict | None:
        cfg = self.store.setting("kaggle_config", None)
        if not cfg:
            return None
        if redact:
            return {**cfg, "api_key": "***" if self._have_key() else None}
        cfg = dict(cfg)
        cfg["api_key"] = self._load_key()
        return cfg

    def _have_key(self) -> bool:
        return bool(self._load_key())

    def _load_key(self) -> str:
        from .providers import keyring
        try:
            return keyring.get_password("BugHunterClaudex", "kaggle_api_key") or ""
        except Exception:
            return ""

    def _loaded(self) -> KaggleConfig:
        cfg = self.config(redact=False)
        if not cfg or not cfg.get("api_key"):
            raise ValueError("Kaggle not configured. Settings → Kaggle finetune → Configure.")
        return KaggleConfig(
            username=cfg["username"],
            api_key=cfg["api_key"],
            notebook_slug=cfg["notebook_slug"],
            dataset_slug=cfg["dataset_slug"],
            output_dataset_slug=cfg["output_dataset_slug"],
        )

    # corpus push ---------------------------------------------------------

    def push_corpus(self, *, corpus_id: str, approved: bool = False) -> dict:
        if not approved:
            raise ValueError("Pushing a corpus to Kaggle requires explicit approval.")
        self.store.require_desktop_owner()
        cfg = self._loaded()
        corpus = self.store.get("corpus", corpus_id)
        if not corpus:
            raise ValueError("Unknown corpus.")
        src = Path(corpus["data_dir"])
        if not src.exists():
            raise ValueError("Corpus data missing on disk.")
        stage = self.root / f"push_{new_id('x')}"
        stage.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            # 1. copy corpus
            for p in src.iterdir():
                if p.is_file():
                    shutil.copy2(p, stage / p.name)
            # 2. write dataset-metadata.json for the Kaggle dataset API
            meta = {
                "title": f"bughunter corpus {corpus.get('name','')}".strip(),
                "id": cfg.dataset_slug,
                "licenses": [{"name": "CC0-1.0"}],
            }
            (stage / "dataset-metadata.json").write_text(json.dumps(meta, indent=2))
            # 3. kaggle API push
            self._kaggle(cfg, ["datasets", "version",
                                "-p", str(stage),
                                "-m", f"corpus {corpus_id} @ {now()}",
                                "--dir-mode", "zip"], description="dataset version")
            # 4. record run
            run_id = new_id("ktrain")
            record = {
                "id": run_id,
                "corpus_id": corpus_id,
                "dataset_slug": cfg.dataset_slug,
                "notebook_slug": cfg.notebook_slug,
                "status": "uploaded",
                "created_at": now(),
            }
            self.store.put("kaggle_run", record, id=run_id)
            self.store.event("kaggle.push_corpus",
                             {"id": run_id, "corpus_id": corpus_id})
            return record
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    # training launch -----------------------------------------------------

    def launch_training(self, *, run_id: str, base_model: str,
                        rank: int = 16, alpha: int = 32, epochs: int = 1,
                        lr: float = 2e-4, batch_size: int = 4,
                        seq_len: int = 4096, approved: bool = False) -> dict:
        if not approved:
            raise ValueError("Launching Kaggle training requires explicit approval.")
        self.store.require_desktop_owner()
        cfg = self._loaded()
        record = self.store.get("kaggle_run", run_id)
        if not record:
            raise ValueError("Unknown kaggle_run.")
        # write a config.json into the notebook dir and push a new notebook version
        stage = self.root / f"nb_{new_id('x')}"
        stage.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            # ship the launcher script from scripts/kaggle/
            launcher = Path(__file__).resolve().parent.parent.parent / "scripts" / "kaggle" / "train_adapter.py"
            if launcher.exists():
                shutil.copy2(launcher, stage / "train_adapter.py")
            else:
                (stage / "train_adapter.py").write_text(_default_script())
            cfg_json = {
                "corpus_dataset": cfg.dataset_slug,
                "output_dataset": cfg.output_dataset_slug,
                "base_model": base_model,
                "rank": rank, "alpha": alpha,
                "epochs": epochs, "lr": lr,
                "batch_size": batch_size, "seq_len": seq_len,
                "run_id": run_id,
            }
            (stage / "config.json").write_text(json.dumps(cfg_json, indent=2))
            (stage / "kernel-metadata.json").write_text(json.dumps({
                "id": cfg.notebook_slug,
                "title": f"bughunter forge trainer",
                "code_file": "train_adapter.py",
                "language": "python",
                "kernel_type": "script",
                "is_private": True,
                "enable_gpu": True,
                "enable_internet": True,
                "dataset_sources": [cfg.dataset_slug],
                "competition_sources": [],
                "kernel_sources": [],
            }, indent=2))
            self._kaggle(cfg, ["kernels", "push", "-p", str(stage)],
                         description="notebook push")
            record = {**record,
                      "status": "training",
                      "base_model": base_model,
                      "training_config": cfg_json,
                      "launched_at": now()}
            self.store.put("kaggle_run", record, id=run_id)
            self.store.event("kaggle.launch_training", {"id": run_id, "base": base_model})
            return record
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    # status + pull -------------------------------------------------------

    def status(self, *, run_id: str) -> dict:
        self.store.require_desktop_owner()
        cfg = self._loaded()
        record = self.store.get("kaggle_run", run_id)
        if not record:
            raise ValueError("Unknown kaggle_run.")
        try:
            out = self._kaggle(cfg,
                               ["kernels", "status", cfg.notebook_slug],
                               description="status poll", capture=True)
        except Exception as exc:
            return {**record, "poll_error": repr(exc)}
        state = _parse_kaggle_status(out)
        record = {**record, "last_status": state, "polled_at": now()}
        self.store.put("kaggle_run", record, id=run_id)
        return record

    def pull_adapter(self, *, run_id: str, name: str,
                     domain: str = "web-audit") -> dict:
        self.store.require_desktop_owner()
        cfg = self._loaded()
        record = self.store.get("kaggle_run", run_id)
        if not record:
            raise ValueError("Unknown kaggle_run.")
        dst = self.root / f"pull_{run_id}"
        dst.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._kaggle(cfg,
                     ["datasets", "download",
                      cfg.output_dataset_slug, "-p", str(dst), "--unzip"],
                     description="dataset download")
        # expect an archive adapter_<run_id>.zip inside dst
        candidates = list(dst.glob("adapter_*.zip"))
        archive = candidates[0] if candidates else _repack(dst, run_id)
        # register with the adapter registry
        rec = self.app.adapters.import_from(
            archive=str(archive),
            name=name,
            domain=domain,
            base_model=record.get("base_model", ""),
            corpus_id=record.get("corpus_id"),
            kaggle_run=run_id,
        )
        record = {**record, "status": "pulled", "adapter_id": rec["id"],
                  "pulled_at": now()}
        self.store.put("kaggle_run", record, id=run_id)
        self.store.event("kaggle.pull_adapter",
                         {"id": run_id, "adapter_id": rec["id"]})
        return {**record, "adapter": rec}

    def list_runs(self) -> list[dict]:
        return self.store.list("kaggle_run")

    # kaggle CLI wrapper --------------------------------------------------

    def _kaggle(self, cfg: KaggleConfig, args: list[str],
                description: str = "", capture: bool = False) -> str:
        """Run the kaggle CLI with credentials written to a scoped
        config dir (never to ~/.kaggle)."""
        env = os.environ.copy()
        cfg_dir = self.root / ".kaggle_conf"
        cfg_dir.mkdir(exist_ok=True, mode=0o700)
        creds = cfg_dir / "kaggle.json"
        creds.write_text(json.dumps({"username": cfg.username,
                                     "key": cfg.api_key}))
        os.chmod(creds, 0o600)
        env["KAGGLE_CONFIG_DIR"] = str(cfg_dir)
        proc = subprocess.run(
            ["kaggle", *args],
            env=env, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise ValueError(f"kaggle {description} failed: {proc.stderr.strip()}")
        return proc.stdout if capture else ""


# --------------------------------------------------------------------------


def _parse_kaggle_status(out: str) -> str:
    low = (out or "").lower()
    for key in ("complete", "error", "cancelled", "running", "queued", "creating"):
        if key in low:
            return key
    return "unknown"


def _repack(folder: Path, run_id: str) -> Path:
    """If the output dataset was unzipped flat, repack into a single zip."""
    archive = folder / f"adapter_{run_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob("*"):
            if p.is_file() and p != archive:
                z.write(p, p.relative_to(folder))
    return archive


def _default_script() -> str:
    """Minimal fallback script if the shipped launcher is missing."""
    return ("import sys\n"
            "print('launcher script not shipped; see scripts/kaggle/train_adapter.py')\n"
            "sys.exit(1)\n")
