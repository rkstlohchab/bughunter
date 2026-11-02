#!/usr/bin/env python3
"""Explicit user-initiated Kaggle download -> local Ollama GGUF installation.

Requires: pip install kagglehub; ollama installed/running. The Kaggle model
must include a compatible GGUF artifact. This script does not choose a license.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def install(handle: str, name: str, file: str | None = None):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+(?:/[0-9]+)?", handle):
        raise ValueError("Use a Kaggle model handle: owner/model/framework/variation[/version].")
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", name):
        raise ValueError("Ollama model name must use letters, numbers, _, :, - or periods.")
    if shutil.which("ollama") is None:
        raise RuntimeError("Install Ollama before importing GGUF models.")
    try:
        import kagglehub
    except ImportError as exc:
        raise RuntimeError("Install kagglehub: python -m pip install kagglehub") from exc
    directory = Path(kagglehub.model_download(handle)).resolve()
    if not directory.is_dir():
        raise ValueError("Kaggle did not provide a model directory.")
    models = sorted(p for p in directory.rglob("*.gguf") if p.is_file() and p.resolve().is_relative_to(directory))
    if file:
        requested = (directory / file).resolve()
        if not requested.is_relative_to(directory) or requested not in models:
            raise ValueError("The requested --file is not a GGUF inside the downloaded model.")
        models = [requested]
    if len(models) != 1:
        choices = "\n".join(str(p.relative_to(directory)) for p in models[:25])
        raise ValueError(f"Found {len(models)} GGUF files; specify one with --file.\n{choices}")
    with tempfile.TemporaryDirectory(prefix="bughunter-ollama-") as tmp:
        manifest = Path(tmp) / "Modelfile"
        manifest.write_text("FROM " + str(models[0]) + "\n", encoding="utf-8")
        subprocess.run(["ollama", "create", name, "-f", str(manifest)], check=True)
    print(f"Installed model {name}. Select Ollama and model '{name}' in the app's Settings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handle", help="Kaggle model handle with framework and variation")
    parser.add_argument("--name", required=True, help="New local Ollama model tag")
    parser.add_argument("--file", help="GGUF relative path when the variation contains multiple")
    a = parser.parse_args()
    try:
        install(a.handle, a.name, a.file)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
