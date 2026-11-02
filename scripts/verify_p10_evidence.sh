#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -m compileall -q backend/bughunter
"$PY" -m pytest -q \
  backend/tests/test_investigation_p10.py \
  backend/tests/test_verification_p9.py \
  backend/tests/test_advanced_p8.py \
  backend/tests/test_readiness_p7.py \
  backend/tests/test_memory_pointer_helpers.py \
  backend/tests/test_offline_game_memory_fixture.py \
  backend/tests/test_staging_price.py \
  backend/tests/test_research_browser_p3.py
bash -n setup-mac.sh scripts/verify_p10_evidence.sh
if [[ -d node_modules/typescript && -d node_modules/react ]]; then
  npm run build
else
  echo 'Full frontend build deferred: npm dependencies not installed. Run npm ci on your Mac.'
fi
echo 'P10 evidence casebook source and fixture checks passed. Native Mac integrations still need on-device verification.'
