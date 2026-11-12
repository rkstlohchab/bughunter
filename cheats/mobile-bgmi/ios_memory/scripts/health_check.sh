#!/usr/bin/env bash
# health_check.sh — verify the dylib loaded + socket reachable on the
# iPhone paired to this Mac.
#
# Usage: health_check.sh [--udid <id>] [--port 65510]

set -euo pipefail
UDID="${1:-}"
PORT=65510

if [[ -z "$UDID" ]]; then
  UDID="$(idevice_id -l | head -n 1 || true)"
fi
if [[ -z "$UDID" ]]; then
  echo "no paired iPhone. Plug in + trust first."
  exit 1
fi

echo "[health] checking iPhone $UDID"

echo "[health] starting a short-lived iproxy on $PORT ..."
pkill -f "iproxy $PORT $PORT" 2>/dev/null || true
iproxy -u "$UDID" "$PORT" "$PORT" >/dev/null 2>&1 &
IPROXY_PID=$!
sleep 0.3

echo "[health] ping the dylib socket ..."
if echo '{"cmd":"ping"}' | nc -w 1 127.0.0.1 "$PORT" | grep -q '"pong":1'; then
  echo "[health] OK — gamelab_memory.dylib is live inside BGMI"
  kill $IPROXY_PID 2>/dev/null || true
  exit 0
else
  echo "[health] FAIL — dylib not responding."
  echo "  - Is the patched BGMI actually running on the iPhone?"
  echo "  - Did AltStore sign succeed (Settings → General → VPN & Device Management)?"
  echo "  - Check Console.app or 'idevicesyslog' for 'gamelab_memory' lines."
  kill $IPROXY_PID 2>/dev/null || true
  exit 2
fi
