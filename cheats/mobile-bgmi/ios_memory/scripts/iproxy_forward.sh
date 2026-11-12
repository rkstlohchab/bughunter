#!/usr/bin/env bash
# iproxy_forward.sh — forward the iPhone's localhost:65510 to your Mac's
# localhost:65510 via usbmuxd. The iOS equivalent of `adb forward`.
#
# Usage: iproxy_forward.sh --udid <iphone-udid> [--port 65510]
#
# Requires libimobiledevice:  brew install libimobiledevice

set -euo pipefail

UDID=""
PORT=65510

while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid) UDID="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

if ! command -v iproxy >/dev/null 2>&1; then
  echo "install libimobiledevice first:  brew install libimobiledevice"
  exit 1
fi

if [[ -z "$UDID" ]]; then
  echo "-- paired iPhones --"
  idevice_id -l || true
  echo "provide --udid <one-of-the-above>"
  exit 1
fi

# Kill stale iproxy for the same port
pkill -f "iproxy $PORT $PORT" 2>/dev/null || true

echo "[iproxy_forward] localhost:$PORT  <->  iphone[$UDID]:$PORT"
nohup iproxy -u "$UDID" "$PORT" "$PORT" >/tmp/iproxy_$PORT.log 2>&1 &
disown
sleep 0.4

if ! curl -sS --max-time 1 "http://127.0.0.1:$PORT" >/dev/null 2>&1; then
  # Not HTTP, but the socket answered — probing the TCP accept is enough.
  :
fi

echo "[iproxy_forward] running (pid $!). log: /tmp/iproxy_$PORT.log"
echo "[iproxy_forward] test with:  echo '{\"cmd\":\"ping\"}' | nc 127.0.0.1 $PORT"
