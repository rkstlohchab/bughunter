#!/usr/bin/env bash
# build_dylib.sh — build gamelab_memory.dylib for iOS arm64.
# Requires: Xcode Command Line Tools (xcode-select --install).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
BUILD="$ROOT/build"
mkdir -p "$BUILD"

SDK=$(xcrun --sdk iphoneos --show-sdk-path)
MIN="14.0"
TARGET="arm64-apple-ios$MIN"

OUT="$BUILD/gamelab_memory.dylib"

clang++ \
  -arch arm64 \
  -isysroot "$SDK" \
  -target "$TARGET" \
  -mios-version-min=$MIN \
  -std=c++17 -fobjc-arc \
  -fvisibility=hidden -O2 -DNDEBUG \
  -framework Foundation -framework UIKit \
  -install_name "@executable_path/Frameworks/gamelab_memory.dylib" \
  -dynamiclib \
  "$ROOT/src/main.mm" \
  "$ROOT/src/socket_server.mm" \
  "$ROOT/src/memory_scanner.mm" \
  "$ROOT/src/touch_injector.mm" \
  "$ROOT/src/antidetect.mm" \
  -o "$OUT"

# Spoof install_name to look like a system telemetry dylib — makes
# anogs's path-based scans see an innocuous entry instead of
# "gamelab_memory". The file on disk stays under Frameworks/ either way.
install_name_tool -id "/usr/lib/system/libsystem_telemetry.dylib" "$OUT" || true

# Strip symbols to shrink + reduce signature surface
strip -x "$OUT" || true

echo "[build_dylib] -> $OUT"
file "$OUT" || true
ls -lh "$OUT"
