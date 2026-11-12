#!/usr/bin/env python3
"""CLI: build a corpus bundle from the operator's local workbench.

Usage:
    python3 scripts/forge_corpus.py --repo /path/to/bughunter --name initial
    python3 scripts/forge_corpus.py --no-repo --no-errors --name traces-only

Writes under the app data directory's ``exports/corpus/<stamp>-<name>/``.
Use the ``kaggle.*`` RPCs (or the Forge UI panel) to push the bundle to
Kaggle for training.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _fix_sys_path() -> None:
    # make ``bughunter`` importable from a source checkout
    here = Path(__file__).resolve().parent
    repo = here.parent
    for candidate in (repo / "backend", repo):
        src = candidate
        if (src / "bughunter").exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))


def main() -> int:
    _fix_sys_path()
    from bughunter.app import Application
    from bughunter.learning_forge import LearningForge

    parser = argparse.ArgumentParser(description="forge a corpus bundle")
    parser.add_argument("--data-dir",
                        help="override BUGHUNTER_DATA_DIR (defaults to ~/Library/Application Support/BugHunterClaudex)")
    parser.add_argument("--name", default=None,
                        help="bundle slug (default: timestamp)")
    parser.add_argument("--repo", default=None,
                        help="repo root for RepoMiner (default: this repo)")
    parser.add_argument("--no-traces", action="store_true")
    parser.add_argument("--no-errors", action="store_true")
    parser.add_argument("--no-repo", action="store_true")
    parser.add_argument("--distill", action="store_true",
                        help="run frontier-model distillation if configured")
    parser.add_argument("--distill-domain", default=None)
    parser.add_argument("--limit-traces", type=int, default=None)
    parser.add_argument("--limit-errors", type=int, default=500)
    parser.add_argument("--limit-repo", type=int, default=2000)
    parser.add_argument("--limit-distill", type=int, default=200)
    args = parser.parse_args()

    if args.data_dir:
        os.environ["BUGHUNTER_DATA_DIR"] = args.data_dir

    # repo root default: this repo
    repo = args.repo
    if not args.no_repo and not repo:
        repo = str(Path(__file__).resolve().parent.parent)

    app = Application(primary=False)
    try:
        include = []
        if not args.no_traces: include.append("traces")
        if not args.no_errors: include.append("errors")
        if not args.no_repo:   include.append("repo")
        forge = LearningForge(app)
        bundle = forge.build(
            include=include,
            repo_root=repo if "repo" in include else None,
            distill=args.distill,
            distill_domain=args.distill_domain,
            name=args.name,
            limits={
                "traces": args.limit_traces,
                "errors": args.limit_errors,
                "repo": args.limit_repo,
                "distill": args.limit_distill,
            },
        )
        print(json.dumps({
            "id": bundle["id"],
            "name": bundle["name"],
            "data_dir": bundle["data_dir"],
            "counts": bundle["counts"],
            "domains": bundle["domains"],
        }, indent=2))
        print(f"\nnext: push to Kaggle via the Forge panel,\n"
              f"      or: python3 -c 'from bughunter.app import Application; "
              f"Application(primary=False).kaggle.push_corpus"
              f"(corpus_id={bundle['id']!r}, approved=True)'")
        return 0
    finally:
        try:
            app.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
