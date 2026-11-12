#!/usr/bin/env bash
# sideload.sh — hand the patched IPA to AltServer or Sideloadly for
# re-signing with your Apple ID and installation on the paired iPhone.

set -euo pipefail

IPA=""
VIA="altstore"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ipa) IPA="$2"; shift 2 ;;
    --via) VIA="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$IPA" || ! -f "$IPA" ]]; then
  echo "usage: $0 --ipa <patched.ipa> [--via altstore|sideloadly]"
  exit 1
fi

case "$VIA" in
  altstore)
    if ! command -v altserver >/dev/null 2>&1; then
      cat <<EOF
AltServer is not on PATH. Set it up once:

  1. Download AltServer for Mac:    https://altstore.io
  2. Open AltServer, click the menu-bar icon → Install AltStore → <your iPhone>.
  3. On the iPhone, open Settings → VPN & Device Management → trust your
     Apple ID's certificate.
  4. Launch AltStore on the iPhone, sign in with the same Apple ID used
     by AltServer.

Then run this script again. For CLI installs you can use:

  altserver -i <iphone-udid> -a <apple-id-email> <IPA>

but the GUI route is friendlier for the first install.
EOF
      exit 2
    fi
    echo "[sideload] handing $IPA to AltServer"
    # If AltServer is running, right-clicking the IPA in Finder → Open With
    # → AltServer installs cleanly. For headless: use altserver CLI
    # (requires Apple ID + 2FA app-specific password).
    open -a AltServer "$IPA" || true
    echo "[sideload] If AltStore prompts your Mac for Apple ID, finish there."
    ;;
  sideloadly)
    echo "[sideload] open Sideloadly, drop the IPA on it, sign in with your Apple ID."
    open -a Sideloadly "$IPA" || {
      echo "Sideloadly not installed. Get it at https://sideloadly.io"
      exit 2
    }
    ;;
  *)
    echo "unknown --via value: $VIA (altstore | sideloadly)"
    exit 1
    ;;
esac
