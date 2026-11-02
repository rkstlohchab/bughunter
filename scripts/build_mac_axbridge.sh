#!/usr/bin/env bash
# Compile the native Accessibility helper locally; never download prebuilt code.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then echo 'Mac-only tool. Run on macOS.' >&2; exit 2; fi
if ! command -v swiftc >/dev/null 2>&1; then echo 'Install Apple Command Line Tools: xcode-select --install' >&2; exit 3; fi
DEST="$HOME/.local/share/bughunterclaudex/bin"
mkdir -p "$DEST"
TMP="$(mktemp "$DEST/.axbridge.XXXXXXXX")"
trap 'rm -f "$TMP"' EXIT
swiftc -O src-tauri/resources/axbridge.swift -o "$TMP" -framework ApplicationServices -framework AppKit
chmod 700 "$TMP"
mv "$TMP" "$DEST/axbridge"
echo "Native app control helper installed: $DEST/axbridge"
echo 'Grant Accessibility to BugHunterClaudex in System Settings. The helper cannot grant itself access.'
