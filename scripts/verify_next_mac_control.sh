#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
[[ "$(uname -s)" == "Darwin" ]] || { echo 'Requires macOS' >&2; exit 2; }
printf '\n1. Compile local native Accessibility adapter\n'
bash scripts/build_mac_axbridge.sh
printf '\n2. Check source/backend regressions\n'
python3 -m compileall -q backend/bughunter examples scripts/verify_external_memory_lab.py
printf '\n3. Frontend and Tauri build\n'
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
printf '\n4. External-process read test (disposable test app only)\n'
python3 scripts/verify_external_memory_lab.py
printf '\nOptional write test (only modifies fixture): python3 scripts/verify_external_memory_lab.py --write-lab\n'
echo 'Next: grant Accessibility, open a test app in Chat -> Control Mac app.'
