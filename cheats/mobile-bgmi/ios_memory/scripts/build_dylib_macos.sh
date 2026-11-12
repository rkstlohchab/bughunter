#!/usr/bin/env bash
# build_dylib_macos.sh — build gamelab_memory_mac.dylib for macOS arm64.
# Same memory scanner + socket server as the iOS build; no UIKit import
# so no touch-injector (Mac games get keyboard+mouse natively).
#
# Requires: Xcode Command Line Tools (xcode-select --install).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
BUILD="$ROOT/build"
mkdir -p "$BUILD"

SDK=$(xcrun --sdk macosx --show-sdk-path)
MIN="11.0"
TARGET="arm64-apple-macos$MIN"

OUT="$BUILD/gamelab_memory_mac.dylib"

clang++ \
  -arch arm64 \
  -isysroot "$SDK" \
  -target "$TARGET" \
  -mmacosx-version-min=$MIN \
  -std=c++17 -fobjc-arc \
  -fvisibility=hidden -O2 -DNDEBUG \
  -DGAMELAB_MACOS_BUILD=1 \
  -framework Foundation \
  -install_name "@executable_path/../Frameworks/gamelab_memory_mac.dylib" \
  -dynamiclib \
  "$ROOT/src/main.mm" \
  "$ROOT/src/socket_server.mm" \
  "$ROOT/src/memory_scanner.mm" \
  "$ROOT/src/antidetect.mm" \
  -o "$OUT"

strip -x "$OUT" || true

# Ad-hoc sign so macOS lets the dylib load
codesign --force --sign - --timestamp=none "$OUT"

echo "[build_dylib_macos] -> $OUT"
file "$OUT"
ls -lh "$OUT"
