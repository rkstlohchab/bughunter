#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -m compileall -q backend/bughunter scripts/run_esp_lab_demo.py
"$PY" -m pytest -q backend/tests/test_advanced_p8.py backend/tests/test_readiness_p7.py
bash scripts/verify_p6_learning.sh
bash -n scripts/verify_p8_advanced.sh scripts/verify_p7_release.sh setup-mac.sh
if [[ "$(uname -s)" == Darwin ]]; then
  echo 'P8: MLX-LM install: python3 -m pip install "mlx-lm[train]" (into the app backend environment)'
fi
if [[ -d node_modules/typescript && -d node_modules/react ]]; then
  npm run build
else
  echo 'Frontend build not run: npm dependencies not installed. Run npm ci first.'
fi
echo 'P8 verification passed for source/lab tests. Native Mac integrations require final machine acceptance.'
