#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
PYTHONPATH=backend "$PY" -m pytest -q \
  backend/tests/test_research_browser_p3.py \
  backend/tests/test_tor_browser.py \
  backend/tests/test_tor_research.py \
  backend/tests/test_course_p0.py \
  backend/tests/test_experiment_p1.py \
  tests/test_p2.py
"$PY" -m compileall -q backend/bughunter
bash -n setup-mac.sh
printf 'P0 + P1 + P2 + P3 focused checks passed. Live Tor Browser/macOS behavior requires Mac testing.\n'
