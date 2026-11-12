#!/usr/bin/env bash
# patch_ipa.sh — inject gamelab_memory.dylib into a BGMI IPA by adding
# an LC_LOAD_DYLIB load command to the main binary, copying the dylib
# into the app's Frameworks/, and rezipping.
#
# Usage:
#   patch_ipa.sh --ipa <input.ipa> --dylib <gamelab_memory.dylib> --out <patched.ipa>
#
# Requires: insert_dylib (brew install insert_dylib), unzip, zip.
# The resulting IPA is NOT signed yet — run sideload.sh next.

set -euo pipefail

IPA_IN=""
DYLIB=""
IPA_OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ipa)   IPA_IN="$2"; shift 2 ;;
    --dylib) DYLIB="$2";  shift 2 ;;
    --out)   IPA_OUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$IPA_IN" || -z "$DYLIB" || -z "$IPA_OUT" ]]; then
  echo "usage: $0 --ipa <stock.ipa> --dylib <gamelab_memory.dylib> --out <patched.ipa>"
  exit 1
fi

if ! command -v insert_dylib >/dev/null 2>&1; then
  echo "install insert_dylib:  brew install insert_dylib"
  echo "or clone: https://github.com/Tyilo/insert_dylib"
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[patch_ipa] unzipping $IPA_IN"
unzip -q "$IPA_IN" -d "$TMP/work"

APP_DIR="$(find "$TMP/work/Payload" -maxdepth 1 -type d -name '*.app' | head -n 1)"
if [[ -z "$APP_DIR" ]]; then
  echo "no .app directory inside IPA"; exit 1
fi
APP_NAME="$(basename "$APP_DIR" .app)"
BIN="$APP_DIR/$APP_NAME"
if [[ ! -f "$BIN" ]]; then
  # Info.plist's CFBundleExecutable is the ground truth; fall back to it
  BIN_NAME="$(/usr/libexec/PlistBuddy -c 'Print CFBundleExecutable' "$APP_DIR/Info.plist" 2>/dev/null || echo "")"
  BIN="$APP_DIR/$BIN_NAME"
fi
if [[ ! -f "$BIN" ]]; then
  echo "cannot find main binary inside $APP_DIR"; exit 1
fi

mkdir -p "$APP_DIR/Frameworks"
cp "$DYLIB" "$APP_DIR/Frameworks/gamelab_memory.dylib"

echo "[patch_ipa] adding LC_LOAD_DYLIB to $BIN"
insert_dylib --inplace --weak \
  '@executable_path/Frameworks/gamelab_memory.dylib' \
  "$BIN"

# Also sign-strip the inserted dylib so the re-signer can re-sign it cleanly.
codesign --remove-signature "$APP_DIR/Frameworks/gamelab_memory.dylib" 2>/dev/null || true
codesign --remove-signature "$BIN" 2>/dev/null || true
# Clean existing CodeSignature dir — AltStore / Sideloadly rewrites it.
rm -rf "$APP_DIR/_CodeSignature" "$APP_DIR/embedded.mobileprovision" 2>/dev/null || true

echo "[patch_ipa] re-zipping -> $IPA_OUT"
(cd "$TMP/work" && zip -qr "$IPA_OUT" Payload)

echo "[patch_ipa] done. Next: sideload.sh --ipa $IPA_OUT --via altstore"
