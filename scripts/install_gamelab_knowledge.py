#!/usr/bin/env python3
"""install_gamelab_knowledge.py — index prompts/game-knowledge into the
KnowledgeService FTS5 + vector store.

Idempotent. Re-run after editing any markdown file in the corpus. Each
file is imported as its own ``source`` entity with tag ``game-knowledge``
in its metadata, so ``gamelab.knowledge_search`` can filter (and also
falls back to a raw markdown grep when FTS has nothing matching).

Uses the local Application without taking the desktop lock
(``primary=False``).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "backend"))

from bughunter.app import Application   # noqa: E402


def main() -> int:
    corpus = HERE / "prompts" / "game-knowledge"
    if not corpus.exists():
        print(f"[install_gamelab_knowledge] not found: {corpus}", file=sys.stderr)
        return 2

    app = Application(primary=False)
    kb = app.knowledge
    imported = 0
    errors: list[str] = []
    for md in sorted(corpus.rglob("*.md")):
        try:
            rec = kb.import_source(path=str(md), copy_original=False)
            # wait synchronously for processing
            if rec.get("id"):
                try:
                    kb.wait_for_source(rec["id"], timeout=60)
                except Exception as exc:
                    errors.append(f"{md.name}: wait failed: {exc}")
            imported += 1
            print(f"  indexed {md.relative_to(HERE)}")
        except Exception as exc:
            errors.append(f"{md}: {exc}")
            print(f"  ERR {md.relative_to(HERE)}: {exc}", file=sys.stderr)

    print(f"[install_gamelab_knowledge] imported {imported} files"
          f" ({len(errors)} errors)")
    for e in errors:
        print(f"  ! {e}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
