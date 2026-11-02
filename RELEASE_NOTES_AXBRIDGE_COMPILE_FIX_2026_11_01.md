# BugHunterClaudex — macOS AXBridge setup compilation hotfix

## Reported failure

`bash setup-mac.sh` failed after Python + npm + Chromium installation while compiling
`src-tauri/resources/axbridge.swift`.

The native Swift compiler reported errors at two `as? AXUIElement` conditional casts,
one for the focused window and one for the menu bar. Swift considers a conditional
downcast to a CoreFoundation type invalid as a type check.

## Code change

`src-tauri/resources/axbridge.swift` now uses `elementAttribute(...)`, which
reads the AX attribute directly, confirms `CFGetTypeID(...) ==
AXUIElementGetTypeID()`, and only then reinterprets the reference as `AXUIElement`.
The two sites use this helper. No device-wide permissions are silently granted.

Regression test: `tests/test_axbridge_cf_types.py`.

## Update without reinstalling dependencies

From the extracted `~/Downloads/bughunter` directory, replace just
`src-tauri/resources/axbridge.swift` with the fixed version, then run:

```bash
bash scripts/build_mac_axbridge.sh
bash setup-mac.sh --core-only
npm run desktop:build
```

The setup script reuses installed Homebrew packages and cached Python/Node/Chromium
dependencies. `--core-only` skips optional research tool installations and the
optional Burp extension build. The project's core Python/Tauri setup still runs.

## What was verified

- Regression assertions, including a check preventing the original invalid casts.
- Swift syntax parsing (`swiftc -frontend -parse`) in a non-macOS build environment.
- ZIP integrity and original source file preservation.

Not verified here: a macOS Swift type-check/build, Accessibility grant behavior,
full Tauri release compilation, and actual automation on the user's Mac.
The proper next validation is `bash scripts/build_mac_axbridge.sh` on macOS.
