#!/usr/bin/env bash
# P7 release gate for the extracted BugHunterClaudex source tree.
# Quick checks run without network access. Full checks include native build on macOS.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="${1:---quick}"
if [[ "$MODE" != "--quick" && "$MODE" != "--full" ]]; then
  echo 'Usage: bash scripts/verify_p7_release.sh [--quick|--full]' >&2
  exit 2
fi
PY="python3"
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
echo '[P7] Python compile and P7 readiness regression'
"$PY" -m compileall -q backend/bughunter
"$PY" -m pytest -q backend/tests/test_readiness_p7.py
bash -n setup-mac.sh scripts/verify_p7_release.sh scripts/verify_p6_learning.sh
printf '\n[P7] Chat routing and frontend parse\n'
if [[ -f node_modules/typescript/package.json && -d node_modules/react && -d node_modules/vite ]]; then
  npm run test:routing
  npm run build
else
  echo 'SKIP: npm dependencies not installed; run npm ci to enable frontend test/build.'
fi
if [[ "$MODE" == "--quick" ]]; then
  echo 'P7 quick checks passed. --full runs the bundled labs and Mac native release gate.'
  exit 0
fi
printf '\n[P7] P0–P6 knowledge, experiment, Tor, and learning tests\n'
bash scripts/verify_p6_learning.sh
printf '\n[P7] Offline memory fixture, read-only (two independent processes)\n'
"$PY" scripts/verify_game_memory_restarts.py
printf '\n[P7] Disposable checkout/price integrity fixture\n'
bash scripts/verify_staging_quote_sandbox.sh
printf '\n[P7] Native macOS release validation\n'
if [[ "$(uname -s)" == 'Darwin' ]]; then
  bash scripts/build_mac_axbridge.sh
  cargo check --manifest-path src-tauri/Cargo.toml
  bash scripts/build_burp_bridge.sh
  npm run desktop:build
  "$PY" scripts/validate_macos_release.py --smoke
else
  echo 'SKIP: native Mac app compilation, Burp extension build, and runtime check need macOS.'
fi
echo 'P7 full source-tree gate completed. Live app, model, Burp, Tor and original-course behavior remain operator acceptance checks.'
