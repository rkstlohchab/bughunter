#!/usr/bin/env bash
# Package the built .so into a Magisk/Zygisk module ZIP.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
OUT="$ROOT/../gamelab_memory.zip"

if [[ ! -f "$ROOT/libs/arm64-v8a/libgamelab_mem.so" ]]; then
  echo "Build first: cd $ROOT && ndk-build -j"
  exit 1
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/module.prop" <<'EOF'
id=gamelab_memory
name=GameLab Memory Bridge
version=v1.0
versionCode=1
author=GameLab
description=On-device memory reader for UE4 mobile games (BGMI/COD-M/PUBG-M/Free Fire). Zygisk-only. Pair with PC-side GameLab via adb forward.
EOF

mkdir -p "$TMPDIR/zygisk"
cp "$ROOT/libs/arm64-v8a/libgamelab_mem.so"    "$TMPDIR/zygisk/arm64-v8a.so"
cp "$ROOT/libs/armeabi-v7a/libgamelab_mem.so"  "$TMPDIR/zygisk/armeabi-v7a.so" || true

cat > "$TMPDIR/customize.sh" <<'EOF'
#!/system/bin/sh
ui_print "- GameLab memory bridge installing"
ui_print "  Requires Zygisk + Shamiko (DenyList enforcing)"
ui_print "  After install, run on PC:"
ui_print "    adb forward tcp:65510 localabstract:gamelab_mem"
EOF

(cd "$TMPDIR" && zip -r "$OUT" .)
echo "Built: $OUT"
