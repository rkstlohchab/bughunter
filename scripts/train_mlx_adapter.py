#!/usr/bin/env python3
"""Fine-tune a domain-specific adapter using exported, reviewed examples.

Operator-initiated on Apple Silicon. Does not modify the app or auto-start.
Requires a local or downloadable MLX-compatible model and mlx-lm[train].
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path


def train(data: Path, model: str, output: Path, iters: int, dry_run=False):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        if not dry_run:
            raise ValueError("MLX LoRA training requires Apple Silicon macOS.")
    data = data.expanduser().resolve()
    manifest_path = data / "manifest.json"
    if not manifest_path.is_file() or not (data / "train.jsonl").is_file():
        raise ValueError("Extract a Training lab dataset ZIP containing manifest.json and train.jsonl.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("training_performed") is not False or manifest.get("domain") not in {"burp_suite", "cheat_engine", "tor_research"}:
        raise ValueError("Unrecognized Training lab export.")
    examples = [json.loads(line) for line in (data / "train.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not examples or any(not isinstance(e.get("messages"), list) or len(e["messages"]) < 2 for e in examples):
        raise ValueError("The training split must contain valid chat examples.")
    if not 1 <= iters <= 20000:
        raise ValueError("Iteration count must be 1..20,000.")
    if not model.strip():
        raise ValueError("Specify an MLX-compatible model or local model directory.")
    output = output.expanduser().resolve()
    cmd = [sys.executable, "-m", "mlx_lm.lora", "--model", model, "--train", "--data", str(data),
           "--iters", str(iters), "--adapter-path", str(output)]
    print(f"Domain: {manifest['domain']}; training samples: {len(examples)}; output: {output}")
    if dry_run:
        print("Command:", " ".join(cmd))
    else:
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Extracted dataset export folder")
    parser.add_argument("--model", required=True, help="MLX-compatible model ID or local folder")
    parser.add_argument("--output", type=Path, default=Path("./adapters/specialist"))
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    a = parser.parse_args()
    try:
        train(a.data, a.model, a.output, a.iters, a.dry_run)
    except (ValueError, subprocess.CalledProcessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
