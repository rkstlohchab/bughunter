#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
PYTHONPATH=backend "$PY" -m pytest -q tests/test_p2.py backend/tests/test_experiment_p1.py backend/tests/test_course_p0.py backend/tests/test_knowledge.py backend/tests/test_resource_imports.py
"$PY" -m compileall -q backend/bughunter
printf 'P0 / P1 / P2 tests passed; verify native Tauri build on macOS.\n'
