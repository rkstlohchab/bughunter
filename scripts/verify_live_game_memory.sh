#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "Checking live memory and fixture source..."
"$PYTHON_BIN" -m compileall -q src-tauri/resources/mac_memory_mcp.py examples/offline_memory_lab.py scripts/run_esp_lab_demo.py scripts/verify_game_memory_restarts.py
if "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  PYTHONPATH=backend "$PYTHON_BIN" -m pytest -q backend/tests/test_live_game_memory_tools.py backend/tests/test_offline_game_memory_fixture.py backend/tests/test_memory_pointer_helpers.py backend/tests/test_module_pointer_resolver.py backend/tests/test_verified_scan_change.py
fi
echo "Running read-only memory restart test on TWO disposable processes..."
"$PYTHON_BIN" scripts/verify_game_memory_restarts.py
echo "Live lab test finished. The native Tauri build and other games still require separate verification."
