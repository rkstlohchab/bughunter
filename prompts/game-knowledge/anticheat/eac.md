---
title: Easy Anti-Cheat (EAC) — Epic / Fortnite / Apex / Rust
tags: [eac, easyanticheat, epic, fortnite, apex, rust, elden-ring, kernel-ac]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Easy Anti-Cheat (EAC)

EAC (Kamu, now Epic) runs in two deployments: kernel-mode (`EasyAntiCheat_EOS.sys` as a game-launched driver) on PC titles that opted in, and usermode-only on Linux/Proton titles. The usermode build is substantially weaker — some single-player games with EAC are trivially cheat-friendly on Linux.

## Kernel build (Windows, most competitive games)

- `EasyAntiCheat.exe` / `EasyAntiCheat_EOS.exe` — launcher process, spins up the driver and the game.
- `EasyAntiCheat_EOS.sys` — kernel driver, not boot-start. Loaded by the launcher.
- Injected DLL in the game process for usermode scans.

Kernel-side:

- Module list enumeration, same technique set as BE/Vanguard
- Kernel callback registration + driver dispatch table scans
- HVCI-compatible (won't load custom unsigned drivers once you have a BYOVD running, but doesn't prevent BYOVD loading itself)
- Protected game process handle watching — see Vanguard behavior

Usermode:

- Module whitelist
- Signature scan of game memory
- Window / overlay enumeration
- Screenshot sampling (less aggressive than BE)

## Linux build (Proton)

- Single usermode process alongside the game, no kernel component on Linux
- Reduced signature coverage
- Steam Deck OS + Proton + EAC Linux: significantly weaker; some reversers consider it "VAC-level" rather than "BE/EAC Windows-level"

## Per-title aggression

- **Fortnite** — moderate on BE build, moderate on EAC build. Behavioral ML on hit pattern.
- **Apex Legends** — EAC + Respawn's homebrew. Historically very porous. Banwaves large.
- **Rust** — EAC + custom Facepunch scanner. Permanent HWID + payment ban.
- **Elden Ring / Dark Souls multiplayer** — EAC + FromSoftware server-side. Toggling EAC off (via `steam_api.dll` tricks) runs offline; multiplayer with EAC off = delisted from matchmaking but not banned.
- **Dead by Daylight, Hunt: Showdown** — EAC classic. Script/overlay territory.

## HWID surface

- SMBIOS UUID
- Disk serials (primary + sometimes all)
- NIC MAC (primary + sometimes all)
- GPU device ID
- Epic Games Launcher install UUID (shared across Epic accounts)

Rust and Apex have the most aggressive HWID enforcement.

## Bypass viability

| Approach | Current lifespan |
|---|---|
| Internal usermode | days |
| Manual map | weeks |
| BYOVD kernel | months on less-watched titles |
| **DMA** | invisible |
| **External vision + HID** | invisible |

## Re-derivation cadence

- Offsets per game update (weekly for Fortnite, biweekly for most)
- EAC driver signature updates — monitor UC per-title subforums

## Linux note

A cheat that runs under Linux on an EAC-protected Proton title typically faces only the usermode scanner. A native Linux usermode cheat (debugger-attached read via `ptrace`, injected via `LD_PRELOAD`) survives dramatically longer. Trade-off: not every EAC title supports Linux — check per-title.
