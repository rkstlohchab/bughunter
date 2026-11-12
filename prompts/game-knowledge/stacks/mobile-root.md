---
title: Mobile root stack — Magisk + Zygisk + ESP tooling (BGMI, COD-M, Free Fire)
tags: [mobile, android, magisk, zygisk, bgmi, root]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Mobile root stack

On a real Android phone, the cheat survives if root is invisible to the game. The reliable path is Magisk + Zygisk + a DenyList-style hider + a build-fingerprint spoofer. Any of these missed = ACE / BE Mobile / Garena shield detects you.

## Hardware

- A phone that supports Magisk root. Pixel line is easiest; Xiaomi/Realme/OnePlus need unlock codes. Samsung Knox bricks the e-fuse on root — avoid.
- A USB cable for adb.
- Optional: a computer to run scrcpy + external vision instead of on-device ESP (see `stacks/external-vision.md`).

## Software stack (on device)

1. **Magisk** (26.x+) — root manager + boot image patcher.
2. **Zygisk** (built into Magisk) — Zygote-side hook infrastructure.
3. **Shamiko** or **Zygisk-Assistant** — hides Magisk, Zygisk, and configured modules from a per-app DenyList.
4. **MagiskHide Props Config** — sets build.prop values per-app to match a known safe device fingerprint.
5. **NoHello** — removes residual tampering fingerprints (SELinux, mount namespaces).
6. **safetynet-fix** or **Universal SafetyNet Fix** — restores SafetyNet BASIC + CTS profile.
7. **PlayIntegrityFix** — restores Play Integrity (needed for some Krafton logins).

## Setup — in order

```bash
# On a computer:
adb reboot bootloader
fastboot flashing unlock           # wipes user data; expected

# Flash patched boot image (Magisk Manager app has "Patch boot image" workflow)
fastboot flash boot magisk_patched.img
fastboot reboot

# Install the following Magisk modules from Magisk Manager:
#  - Shamiko (or Zygisk-Assistant)
#  - MagiskHide Props Config
#  - NoHello
#  - safetynet-fix
#  - PlayIntegrityFix

# In Magisk Manager > Settings:
#  - Zygisk: on
#  - Enforce DenyList: on
#  - DenyList: add com.pubg.imobile, com.tencent.ig, com.garena.game.codm, etc.

# In Shamiko mode: whitelist mode OFF (Shamiko hides everything in DenyList)
```

Verify before launching the game:

```bash
adb shell su -c "getprop ro.build.fingerprint"   # should match chosen stock phone
adb shell su -c "getprop ro.debuggable"          # must be 0
adb shell su -c "getprop ro.secure"              # must be 1
# SafetyNet check via SafetyNet Attestation Checker app — BASIC + CTS pass
# Play Integrity check via Play Integrity API Checker app — MEETS_DEVICE_INTEGRITY + MEETS_STRONG_INTEGRITY
```

Any failure → fix before game launch. A failed launch flag is remembered server-side.

## Build fingerprint profiles

MagiskHide Props Config has preset profiles. Pick a profile matching a phone physically similar to yours (same chipset family, same Android version year) — mismatched fingerprints are a flag.

Example profile for a mid-range 2024 Snapdragon device:

```
ro.product.brand=google
ro.product.manufacturer=Google
ro.product.model=Pixel 7
ro.product.name=panther
ro.product.device=panther
ro.build.fingerprint=google/panther/panther:14/UP1A.231105.001/10873302:user/release-keys
ro.build.id=UP1A.231105.001
ro.build.version.release=14
ro.build.version.sdk=34
ro.build.version.security_patch=2023-11-05
ro.build.tags=release-keys
ro.build.type=user
ro.debuggable=0
ro.secure=1
ro.build.selinux=1
```

## ESP / cheat options on rooted device

1. **GameGuardian** — memory scanner. Add to DenyList + run through Shamiko. Scripts write cheat values to found addresses. Easy. Detected after 1-3 weeks typically.
2. **Native hook via LSPosed/EdXposed** — hook game's own render/input functions. More durable. Build time.
3. **Zygisk native module** — a native `.so` injected into the game process via Zygisk. Hard to detect if you keep your hooks off the default inline-hook signature set. Weeks-months lifespan.
4. **External pipeline** — scrcpy mirror → PC YOLO → ADB input. The phone runs cheat-free; the vision sits off-device. Invisible to anogs. See `cheats/mobile-bgmi/bgmi_esp.py`.

## When to go external instead

| Path | Pro | Con |
|---|---|---|
| On-device (Zygisk native) | Full control of game process | Lifespan weeks; anogs updates monthly |
| External (scrcpy + PC) | Invisible to game; works on non-rooted phone | ~80-120ms latency; sensor fingerprint still gates competitive tier |
| External (OTG mouse + mobile YOLO) | Connected-device tier doesn't care about mouse | Lower competitive tier |

**For BGMI competitive ranked, the external path is the only one with multi-month lifespan.**

## COD Mobile / Free Fire specifics

- **COD-M (Activision + Tencent ACE)** — same stack as BGMI anogs. Safety net required to pass. Rooted root-hidden phone survives 2-4 weeks between adjustments.
- **Free Fire (Garena shield)** — aggressive device-fingerprint. Aggressive IP-region matching. Garena permanent bans the IMEI (requires device swap to recover).

## Re-derivation cadence

- Magisk hide bypass — monthly
- Build fingerprint profile — seasonally
- Game offsets (for native hook paths) — per game update

## Sources

- XDA Forums → Magisk, Zygisk
- https://github.com/LSPosed/LSPosed
- https://github.com/kdrag0n/safetynet-fix
- https://github.com/chiteroman/PlayIntegrityFix
- UC PUBGM / COD-M / FF forums
