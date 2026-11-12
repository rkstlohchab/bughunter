#!/usr/bin/env bash
# patch_mac_app.sh — patch a Mac App Store or Steam .app bundle to load
# the gamelab_memory_mac.dylib on launch.
#
# Flow:
#   1. Copy the stock .app to a working directory (NEVER modify /Applications)
#   2. insert_dylib adds LC_LOAD_DYLIB pointing to the dylib
#   3. Copy the dylib into Contents/Frameworks/
#   4. codesign --force --deep with entitlements that disable library
#      validation + allow unsigned exec memory
#
# After: `open <patched.app>` — first launch may need right-click → Open
# for Gatekeeper to clear.
#
# Usage:
#   patch_mac_app.sh --app "/Applications/Among Us.app" \
#                    --dylib build/gamelab_memory_mac.dylib \
#                    --out "~/gamelab-work/Among Us.app"

set -euo pipefail

APP_IN=""
DYLIB=""
APP_OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)   APP_IN="$2";  shift 2 ;;
    --dylib) DYLIB="$2";   shift 2 ;;
    --out)   APP_OUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$APP_IN" || -z "$DYLIB" || -z "$APP_OUT" ]]; then
  echo "usage: $0 --app <stock.app> --dylib <gamelab_memory_mac.dylib> --out <patched.app>"
  exit 1
fi

if ! command -v insert_dylib >/dev/null 2>&1; then
  echo "install insert_dylib:  brew install insert_dylib"
  exit 1
fi

# Expand ~ in APP_OUT
APP_OUT="${APP_OUT/#\~/$HOME}"
APP_IN="${APP_IN/#\~/$HOME}"
DYLIB="${DYLIB/#\~/$HOME}"

echo "[patch_mac_app] copying $APP_IN -> $APP_OUT"
mkdir -p "$(dirname "$APP_OUT")"
rm -rf "$APP_OUT"
cp -R "$APP_IN" "$APP_OUT"

# Find the main binary
BIN_NAME="$(/usr/libexec/PlistBuddy -c 'Print CFBundleExecutable' "$APP_OUT/Contents/Info.plist")"
BIN="$APP_OUT/Contents/MacOS/$BIN_NAME"
if [[ ! -f "$BIN" ]]; then
  echo "cannot find main binary: $BIN"
  exit 2
fi

echo "[patch_mac_app] inserting LC_LOAD_DYLIB into $BIN"
insert_dylib --inplace --weak \
  '@executable_path/../Frameworks/gamelab_memory_mac.dylib' \
  "$BIN"

mkdir -p "$APP_OUT/Contents/Frameworks"
cp "$DYLIB" "$APP_OUT/Contents/Frameworks/gamelab_memory_mac.dylib"

# Entitlements to defeat Hardened Runtime library-validation
ENT="$APP_OUT/../gamelab_entitlements.plist"
cat > "$ENT" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.disable-library-validation</key><true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
    <key>com.apple.security.cs.disable-executable-page-protection</key><true/>
    <key>com.apple.security.network.client</key><true/>
    <key>com.apple.security.network.server</key><true/>
</dict>
</plist>
EOF

# Strip existing signature then re-sign ad-hoc with the entitlements
echo "[patch_mac_app] re-signing ad-hoc with library-validation off"
codesign --remove-signature "$APP_OUT" 2>/dev/null || true
codesign --force --deep --sign - --entitlements "$ENT" --timestamp=none "$APP_OUT"

# Verify
codesign -dv --entitlements - "$APP_OUT" 2>&1 | sed -n '1,8p' || true

# Remove Mac App Store metadata that fails post-modification (optional)
rm -rf "$APP_OUT/Contents/_MASReceipt" 2>/dev/null || true

echo "[patch_mac_app] done."
echo "  Launch:   open \"$APP_OUT\""
echo "  If Gatekeeper blocks: right-click the app in Finder → Open → Open anyway"
