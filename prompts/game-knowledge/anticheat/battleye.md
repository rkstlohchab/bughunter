---
title: BattlEye (BE)
tags: [battleye, r6-siege, dayz, tarkov, arma, kernel-ac]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# BattlEye (R6 Siege, DayZ, Tarkov, ARMA, Fortnite's BE build)

BattlEye is a long-running kernel-mode anticheat. Less aggressive than Vanguard at boot but more aggressive than VAC at runtime, with a usermode scanner that updates signatures roughly daily.

## Components

- `BEDaisy.sys` — kernel driver loaded by the game launcher, not at boot. Can be stopped between sessions but is required to play.
- `BEService.exe` — usermode service, communicates with `BEDaisy.sys` via IOCTL.
- `BattlEye.dll` (or a game-specific name) — injected into the game process; scans module list, patterns, hooks.

## Kernel scans (BEDaisy.sys)

- Module enumeration via `PsLoadedModuleList` + driver dispatch table walking
- SSDT/IDT integrity
- Protected process handles — any handle to the game with `PROCESS_VM_READ` from an unrecognized process is flagged
- Open memory sections matching the game's name
- Physical memory read endpoints — BE will see `ZwOpenSection` of `\Device\PhysicalMemory`
- Kernel callback presence — BE itself registers `PsSetCreateProcessNotifyRoutineEx`

## Usermode scans

- **Signature scan.** Regular (every few seconds) scan of the game process's memory region for known cheat byte patterns. The signature database updates daily — flagged rigs often sit for 1-7 days before a wave ban hits.
- **Loaded module whitelist.** Any unsigned or non-whitelisted DLL in the game process = flag.
- **Window enumeration.** Fullscreen transparent windows over the game = flag.
- **Screenshot comparisons.** Sporadic screenshots sent server-side, hashed and compared against known ESP overlays.

## Wave-ban model

BE collects evidence for days-weeks before issuing a ban wave. A player who's been running a cheat for 2 weeks and suddenly stops still receives the ban if a signature match was logged in that window. This makes "test for a week then stop" a bad opsec — the logged match still fires.

Game-specific:

- **R6 Siege** — macro + reticle-drift ML on top of BE signature.
- **Tarkov** — extremely aggressive. BE here includes a custom module that scans for radar/ESP network-fingerprint plus memory signature. Tarkov ban = permanent HWID + account + IP cluster.
- **DayZ** — more lenient, mod-friendly; BE accepts whitelisted signed mods.

## Bypass surface

| Approach | Current lifespan |
|---|---|
| Internal usermode | hours-days (signature wave) |
| Manual-mapped | days-weeks |
| BYOVD + kernel reads | weeks-months |
| **DMA** | **invisible to BE; behavioral still possible** |
| **External vision + HID** | **invisible to BE; behavioral still possible** |

## HWID surface

BattlEye collects on ban:
- SMBIOS UUID
- Disk serials
- NIC MACs
- GPU PCI ID
- Windows installation ID (shared across accounts on same OS)

Tarkov's BE has the most aggressive HWID enforcement of any BE-protected game.

## Re-derivation cadence

- Offsets — per game update (weekly for Tarkov, monthly for Siege, variable for others)
- Signature flags — monitor UC daily for which modules are being sig-detected this week
- BYOVD driver — Microsoft's block list sweeps BE-usable drivers often

## Sources

- https://www.battleye.com/support/ — official but sparse
- UnknownCheats BE subforum per game
