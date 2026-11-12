# GameLab Hide — Magisk module skeleton

This directory is a template for a Magisk module that complements Shamiko /
Zygisk-Assistant / MagiskHide Props Config in hiding root from BGMI, COD-M,
Free Fire, and other mobile games with ACE-class detection.

It does NOT replace Shamiko. Shamiko is what actually hides Magisk from a
DenyList of apps. This module ships the suggested DenyList entries and a
build-prop profile for a known-stock Pixel 7 that games currently don't
flag.

## Build

```bash
cd cheats/mobile-bgmi/magisk_hide
zip -r ../gamelab_hide.zip module.prop README.md system.prop customize.sh
```

Then flash via Magisk Manager → Modules → Install from storage.

## What it does

- Adds a `system.prop` with the Pixel 7 build-fingerprint profile
- Emits `customize.sh` that writes the DenyList entries for BGMI / COD-M / FF
- Does NOT touch `/system` mount or SELinux

## Dependencies

- Magisk ≥ 26.1
- Zygisk enabled (Magisk → Settings → Zygisk ON, Enforce DenyList ON)
- Shamiko module installed
- (recommended) safetynet-fix + PlayIntegrityFix modules

## Verification after install

```bash
# Via adb from a PC:
adb shell "getprop ro.build.fingerprint"   # should match Pixel 7 profile
adb shell "getprop ro.debuggable"          # must be 0
adb shell "getprop ro.secure"              # must be 1
adb shell pm list packages | grep magisk   # should be empty (hidden)
adb shell su -c "which su"                 # should fail or empty (hidden)
```

All of the above also show up in `gamelab bgmi_mobile safety_check`.
