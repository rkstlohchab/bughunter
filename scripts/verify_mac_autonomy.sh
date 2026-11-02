#!/usr/bin/env bash
# Developer self-check: deliberately does not control apps, scan websites, or write process memory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo 'BugHunterClaudex — local build readiness'
echo '1) Python syntax'
python3 -m compileall -q backend/bughunter
if [[ -x backend/.venv/bin/python ]]; then
  echo '2) Focused regression tests'
  PYTHONPATH=backend backend/.venv/bin/python -m pytest -q \
    backend/tests/test_burp_repeater_and_passive_review.py \
    backend/tests/test_burp_active_control.py \
    backend/tests/test_autopilot_chat_first.py \
    backend/tests/test_mac_tool_handoff_inventory.py
else
  echo '2) Backend virtual environment missing: run python3 scripts/setup.py'
fi
if [[ -d node_modules && -x node_modules/.bin/tsc ]]; then
  echo '3) Frontend build'
  npm run build
  npm run test:routing
else
  echo '3) Frontend dependencies missing: npm ci (or python3 scripts/setup.py)'
fi
if command -v cargo >/dev/null 2>&1; then
  echo '4) Native Tauri compilation'
  cargo check --manifest-path src-tauri/Cargo.toml
else
  echo '4) Rust toolchain missing: install Rust before building the desktop app'
fi
if command -v javac >/dev/null 2>&1; then
  echo '5) Burp Montoya extension (requires network if API JAR is not cached)'
  bash scripts/build_burp_bridge.sh
else
  echo '5) Java compiler missing: install a supported JDK'
fi
echo 'Checks finished. Real macOS permissions, Burp Pro and Cheat Engine still require manual live verification.'
