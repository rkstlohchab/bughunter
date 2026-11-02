#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -m compileall -q backend/bughunter scripts/run_esp_lab_demo.py
"$PY" -m pytest -q \
    backend/tests/test_verification_p9.py \
    backend/tests/test_advanced_p8.py \
    backend/tests/test_readiness_p7.py \
    backend/tests/test_research_browser_p3.py \
    backend/tests/test_memory_pointer_helpers.py \
    backend/tests/test_offline_game_memory_fixture.py \
    backend/tests/test_commerce_audit.py
if [[ -d node_modules/typescript && -d node_modules/react ]]; then
  npm run build
else
  echo 'Frontend full build deferred: run npm ci, then npm run desktop:build.'
fi
bash -n setup-mac.sh scripts/verify_p9_advanced.sh
echo 'P9 source, fixture and evidence tests passed. Mac native features still require on-device final verification.'
