#!/usr/bin/env bash
# Local-only source and build verification. Never modifies the OS or targets apps.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
echo "Platform: $(uname -s)"
echo "Checking local Python sources and focused tests..."
python3 -m compileall -q backend/bughunter src-tauri/resources/mac_memory_mcp.py
echo "Running actual memory helper read/write/restore test against its own process..."
python3 scripts/verify_memory_engine_live.py
if python3 -m pytest --version >/dev/null 2>&1; then
  PYTHONPATH=backend python3 -m pytest -q \
    backend/tests/test_desktop_planner_expanded.py \
    backend/tests/test_verified_scan_change.py \
    backend/tests/test_memory_bridge_self_test.py \
    backend/tests/test_memory_pointer_helpers.py \
    backend/tests/test_live_game_memory_tools.py \
    backend/tests/test_offline_game_memory_fixture.py \
    backend/tests/test_module_pointer_resolver.py
else
  echo "pytest unavailable; run python3 -m pip install pytest to run the focused tests."
fi
if command -v npm >/dev/null && test -d node_modules; then
  echo "Checking React/TypeScript build..."
  npm run build
else
  echo "Frontend dependencies not installed: run npm ci first."
fi
if test "$(uname -s)" = "Darwin" && command -v cargo >/dev/null; then
  echo "Checking native Tauri/Rust source on this Mac..."
  (cd src-tauri && cargo check)
else
  echo "Native Tauri verification needs macOS and Cargo."
fi
echo "Checks finished. Real app permissions and game-process access still require hands-on testing."
