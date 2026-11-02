#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
PYTHONPATH=backend "$PYTHON" -m pytest -q backend/tests/test_course_p0.py backend/tests/test_knowledge.py backend/tests/test_local_media_runtime.py backend/tests/test_resource_imports.py
"$PYTHON" -m compileall -q backend/bughunter
printf '\nP0 course-learning backend checks passed. Full Tauri/macOS integration still requires the desktop build.\n'
