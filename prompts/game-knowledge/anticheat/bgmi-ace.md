---
title: BGMI / PUBG Mobile — ACE anticheat + emulator detection
tags: [bgmi, pubg-mobile, tencent, ace, mobile-ac, emulator-detection]
source: in-house
updated: 2026-11-12
lifespan_class: days
---

# BGMI / PUBGM anticheat (ACE_ANTICHEAT + GameGuard family)

BGMI (the Indian re-release of PUBG Mobile under Krafton/Level Infinite, with Tencent's ACE stack underneath) layers four distinct checks: device integrity, process environment, memory integrity, and sensor/behavioral fingerprint. Each is independently detectable on real-device runs and in emulators.

## Native device anticheat (ACE_ANTICHEAT, libanogs)

Shipped as a native library (`libanogs.so`, `libanort.so`) bundled with the game. Runs inside the game process. Scans:

- `/proc/self/maps` for known tool signatures (GameGuardian, Frida, Xposed, LSPosed, EdXposed, Lucky Patcher, VirtualXposed)
- `/proc/self/status` for `TracerPid` (Frida or any debugger attached → nonzero)
- `/proc/self/mem` integrity hashes of the game's own code pages
- Open file descriptors for devices suspicious tools use (`/dev/mem`, `/dev/kmem`, `/dev/ptmx` with suspicious patterns)
- `getprop` output for ro.debuggable, ro.secure, ro.build.type (should be `user`, not `userdebug`)
- SELinux state — enforcing required; permissive = flag
- Package manager — looks for Magisk app signatures, SuperSU, com.topjohnwu.magisk, etc.
- Mounted filesystems — `/system` remount-rw is a flag
- `su` binary path scan

## Magisk-hiding path

The current survival path on rooted Android is Magisk + a DenyList-style module:

1. **Magisk** (24.x+) installed via patched boot image.
2. **Shamiko** or **Zygisk-Assistant** module — hides Magisk from `com.pubg.imobile`.
3. **MagiskHide Props Config** — sets `ro.build.fingerprint` to a known-safe stock value matching the device.
4. **NoHello** — removes residual `/system` tampering fingerprints.
5. Add the BGMI package to Zygisk DenyList.
6. Verify with `safetynet-fix` → SafetyNet BASIC + CTS pass before launching game.

Even with this, ACE periodically pushes new hiding-bypass scans. Expected lifespan on a rooted real device: 1-4 weeks between adjustments.

## Emulator detection (TGPA / Tencent ACE for PC)

GameLoop (Tencent's blessed emulator) runs BGMI with TGPA (Tencent Gaming Protection Agent). Third-party emulators (BlueStacks, MEmu, LDPlayer, Nox) get fingerprinted via:

- CPU feature set — ARMv8 features advertised vs actually executed; x86-translated ARM has telltales
- Sensor data — real phones have gyro/accel noise baseline ≈ 0.001–0.01 g on every axis; emulators emit flat zeros or injected sinusoids
- Frame timing — real GPU vsync has jitter; QEMU-backed emulators have artifacts
- Touch events — real touches have `pressure`, `tool_major`/`tool_minor`, `orientation` fields; emulated taps have default zeros unless the emulator spoofs
- Build props — must match a known phone's exact set
- Baseband info — real phones expose IMEI, SIM state, carrier info; emulators often fake wrong
- Network interface — real phone has `rmnet0`, `wlan0`; emulator has `eth0` tagged wifi

**Matched-lobby demotion.** BGMI silently queues emulator-flagged accounts into emulator-only lobbies (no real players), which is Tencent's preferred ban: no visible flag, user just notices everyone's a bot.

## Memory integrity

- Critical globals (player count, team, HP, position arrays) have shadow hashes rechecked every 10-30 seconds.
- Script-engine (Lua) hooks for custom scripts via GameGuardian are detected — write to a hashed region → next hash check fails → kick + flag.

## Behavioral signals

- No-recoil detection via frame-by-frame reticle drift analysis
- Auto-aim detection via mouse-trajectory ML (same model as PC PUBG)
- Headshot % against bots (TDM, warmup) is logged separately — unusually-perfect bot HS = flag
- Server-side position validation — teleports or speed > map-allowed are rubber-banded and logged

## Build fingerprint spoofing

To pass device integrity on a rooted phone, every one of these must line up with the chosen stock model:

```
ro.product.brand
ro.product.manufacturer
ro.product.model
ro.product.name
ro.product.device
ro.build.fingerprint
ro.build.id
ro.build.version.release
ro.build.version.sdk
ro.build.version.security_patch
ro.build.tags             → must be "release-keys"
ro.build.type             → must be "user"
ro.debuggable             → must be "0"
ro.secure                 → must be "1"
```

Mismatches are a strong signal. Use a MagiskHide Props Config profile matching the exact phone (same chipset, same ROM year) — see `stacks/mobile-root.md`.

## What survives (2026)

| Approach | Lifespan |
|---|---|
| Emulator + script (GameLoop / GameGuardian) | hours–days |
| Rooted real device + Magisk hide + ESP/script | 1-4 weeks between adjustments |
| Rooted + Zygote hook + custom native module | 2-6 weeks |
| **External: scrcpy mirror + YOLO on PC + ADB input** | **invisible to game** (sensor fingerprint still gates competitive tiers) |
| **External + sensor spoofing via Frida hook** | **weeks** (Frida on device still detected by anogs unless hidden) |
| **OTG mouse + mobile vision (connected-device tier)** | **invisible** (game allows mouse on that tier, just matched vs mouse players) |

The invisible paths require the vision runs on a second device that the game can't see.

## Re-derivation cadence

- Game offsets — per update (every 2-3 weeks for BGMI)
- Build prop profile — per season (every 2-3 months a new recommended stock fingerprint set)
- Sensor spoofing hook — per `libanogs.so` update (monthly)

## Sources

- XDA forums Magisk/Zygisk threads — fresh hide bypasses
- UnknownCheats PUBGM subforum
- GitHub: `kdrag0n/safetynet-fix`, `LSPosed/LSPosed`
