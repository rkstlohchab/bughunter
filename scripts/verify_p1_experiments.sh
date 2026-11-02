#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="python3"
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
PYTHONPATH=backend "$PY" -m pytest -q \
  backend/tests/test_experiment_p1.py \
  backend/tests/test_course_p0.py \
  backend/tests/test_knowledge.py \
  backend/tests/test_local_media_runtime.py \
  backend/tests/test_resource_imports.py \
  backend/tests/test_commerce_audit.py
"$PY" -m compileall -q backend/bughunter
printf '\nP0/P1 tests passed. Build the Tauri app separately on macOS.\n'
