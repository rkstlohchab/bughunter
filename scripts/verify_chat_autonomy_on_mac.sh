#!/usr/bin/env bash
# Safe, local preflight for Apple Silicon. No website requests, no process attach,
# no memory modifications, no app-control commands and no secrets printed.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

printf '\nBugHunterClaudex — Mac Chat Autonomy Verification\n'
printf 'Host: %s / %s\n' "$(uname -s)" "$(uname -m)"
if [[ "$(uname -s)" != 'Darwin' ]]; then
  echo 'ERROR: This verification is intended for macOS.' >&2
  exit 2
fi

have() { command -v "$1" >/dev/null 2>&1; }
missing=0
for tool in python3 node npm cargo uv javac; do
  if have "$tool"; then echo "OK: $tool available"; else echo "MISSING: $tool"; missing=1; fi
done
if (( missing )); then
  echo 'Install missing developer dependencies before running a full desktop build.' >&2
  exit 3
fi

if [[ ! -x backend/.venv/bin/python || ! -d node_modules ]]; then
  echo 'Dependencies missing: run python3 scripts/setup.py first.' >&2
  exit 4
fi

printf '\n1) Backend regression and macOS memory self-test\n'
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q \
  backend/tests/test_burp_composite_autonomy.py \
  backend/tests/test_desktop_receipts_autonomy.py \
  backend/tests/test_desktop_share_boundary.py \
  backend/tests/test_autopilot_chat_first.py \
  backend/tests/test_memory_bridge_self_test.py \
  backend/tests/test_burp_active_control.py
backend/.venv/bin/python scripts/verify_memory_engine_live.py

printf '\n2) TypeScript and frontend build\n'
npm run build
printf '\n3) Tauri native Rust check\n'
cargo check --manifest-path src-tauri/Cargo.toml

printf '\n4) Burp extension compile against official Montoya API\n'
bash scripts/build_burp_bridge.sh

printf '\nLOCAL BUILD CHECKS PASSED.\n'
echo 'Live checks still required inside the launched Mac app:'
echo '  • macOS Accessibility and Screen Recording approval'
echo '  • Open a test app; use Chat -> Control a Mac app -> Test app access'
echo '  • Settings -> Memory helper -> Test memory reader'
echo '  • Start Burp Professional and load the generated bridge extension'
echo '  • Authorize a lab project and ask Chat to review Burp website findings'
echo '  • Use only your own offline memory-test process for write verification'
echo 'This script does not confirm control of Burp Pro, Cheat Engine or arbitrary games.'
