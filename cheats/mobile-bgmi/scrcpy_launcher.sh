#!/usr/bin/env bash
# scrcpy_launcher.sh — one-shot start for the BGMI mirror window on a PC.
# Usage: ./scrcpy_launcher.sh [serial] [max-fps] [bitrate]
# Defaults: autodetect, 60 fps, 20M bitrate.

set -euo pipefail

SERIAL="${1:-}"
MAX_FPS="${2:-60}"
BIT_RATE="${3:-20M}"
STAMP="$(date +%s)"
TITLE="GameLab-BGMI-${STAMP}"

if ! command -v scrcpy >/dev/null 2>&1; then
  echo "scrcpy not installed. install:"
  echo "  macOS:   brew install scrcpy android-platform-tools"
  echo "  Linux:   apt install scrcpy adb      # or build from source"
  echo "  Windows: download https://github.com/Genymobile/scrcpy/releases"
  exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "adb missing; install android-platform-tools"
  exit 1
fi

if ! adb devices | awk 'NR>1 && $2=="device" {found=1} END{exit !found}'; then
  echo "no adb device in device state. Check:"
  echo "  - USB cable (data, not power-only)"
  echo "  - Developer Options > USB debugging on"
  echo "  - Authorize PC on phone when prompted"
  exit 1
fi

ARGS=(
  --max-fps "$MAX_FPS"
  --video-codec h265
  --video-bit-rate "$BIT_RATE"
  --no-audio
  --window-title "$TITLE"
  --window-borderless
)
[[ -n "$SERIAL" ]] && ARGS+=(--serial "$SERIAL")

echo "[scrcpy] launching mirror window titled: $TITLE"
scrcpy "${ARGS[@]}" &
SCRCPY_PID=$!
echo "[scrcpy] pid=$SCRCPY_PID — pass --window-title '$TITLE' to bgmi_esp.py"
wait "$SCRCPY_PID"
