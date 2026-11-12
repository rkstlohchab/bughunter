---
title: Riot Vanguard — detection vectors and surfaces
tags: [vanguard, valorant, riot, kernel-ac]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Riot Vanguard (vgk.sys)

Vanguard is the most aggressive consumer-grade anticheat shipping today. It loads as a kernel driver at Windows boot — before any usermode process starts — and refuses to run the game if its driver was not loaded clean from boot. Shutting down `vgk.sys` requires a reboot. On Windows 11, Vanguard additionally enforces TPM 2.0, Secure Boot, and HVCI (hypervisor-protected code integrity).

## Boot path

`vgk.sys` is signed and registered as a boot-start driver (`SERVICE_BOOT_START`). It initializes before the Windows subsystem, which means any usermode cheat that tries to load first has already lost. Anti-rootkit-style disable-at-boot attempts leave a fingerprint Vanguard sees when the game launches.

A Type-1 hypervisor (or a UEFI bootkit) is the only software vector that genuinely runs before Vanguard — see `stacks/hypervisor.md`.

## Usermode companion (vgc.exe)

`vgc.exe` runs as a Windows service with `SERVICE_INTERACTIVE_PROCESS`-like privilege and is a protected process light (PPL). It hosts the user-facing event sink. It:

- Subscribes to ETW (Event Tracing for Windows) streams: process creation, image load, driver load, DNS, TCP connects, registry writes.
- Registers `PsSetCreateProcessNotifyRoutineEx` and `PsSetLoadImageNotifyRoutine` callbacks via `vgk.sys` to see every new process and every DLL load on the system.
- Walks running processes and compares module lists against a signature set (updated server-side).
- Reports telemetry in encrypted TLS blobs to Riot's anticheat servers. Blocking the domain = can't play.

## Kernel-side scans (vgk.sys)

- **Module list walk.** `PsLoadedModuleList` is enumerated; unsigned, manually-mapped, or hidden drivers are flagged. Manual-mapped drivers (no PE in `PsLoadedModuleList`) are detected by walking SSDT jmp targets, driver dispatch tables, and `ExAllocatePool` allocations whose protection is `RWX`.
- **SSDT/IDT integrity.** Jump targets checked against the known-good set hashed at boot.
- **Page integrity.** Known kernel page hashes compared against runtime; patched kernel functions (common evasion technique) are detected.
- **Driver dispatch walk.** `IRP_MJ_*` dispatch tables scanned for hooks.
- **Handle enumeration.** `ObReferenceObjectByHandle` chains walked to find handles to game process with `PROCESS_VM_READ`, `PROCESS_VM_WRITE`, `PROCESS_VM_OPERATION`.
- **System thread enumeration.** `PsGetCurrentProcessId` walked to find kernel threads outside expected ranges.
- **HVCI + KMCI.** With Windows 11 enabled, unsigned drivers can't load at all without BYOVD (Bring Your Own Vulnerable Driver).

## Usermode scans

- **Loaded module whitelist.** Game process's module list is walked; non-whitelisted modules = flag.
- **Window/handle enumeration.** Enumerates windows that overlay the Valorant window; transparent fullscreen windows above the game = flag.
- **Screenshot hashing.** Vanguard can take screenshots of the game surface and compare against reference captures (detect ESP overlays).
- **Input device ACL.** `CreateFile` on `\Device\RawInput` monitored; synthetic input devices are checked against a known driver allowlist.

## Behavioral layer

- **Input telemetry.** Mouse polling cadence, acceleration curves, pre-aim angles, snap-to-target velocities, fire-after-aim delta. ML model flags outliers.
- **Flick accuracy distribution.** Players at a given rank fall in a known distribution — top-1% HS%, flick-shot accuracy, pre-fire ratio. Outside the top-0.5% gets a review.
- **Map awareness.** Looking at walls where enemies are visible through them, before they're announced visible, is a strong ESP tell.
- **Reaction floor.** Time between enemy pixel-visible and crosshair-on-target. Humans sit at 180–240ms; below 140ms gets a flag.

## HWID ban surface

On ban, Vanguard records and refuses from that machine:

- SMBIOS UUID (motherboard) — `bios`, `system`, `baseboard`, `chassis` UUIDs
- Disk serial numbers — all attached disks (not just the boot disk)
- NIC MAC addresses — every physical NIC seen
- GPU UUID and device ID
- CPU serial/microcode revision
- TPM public endorsement key hash (Windows 11)

Any one of these, re-seen on a new Riot account, chains the ban. Spoofing requires every item on the list — see `bypass/hwid-spoofing.md`.

## Account linkage

Ban graph edges:

- Payment instrument (card hash, PayPal email)
- Login IP distribution over time
- Riot ID family (recovery emails, 2FA phone)
- Twitch/Discord OAuth history
- Friends graph — a fresh account that queues with known-cheater accounts gets linked

A clean account on banned hardware is caught by HWID; clean hardware on a linked payment/email is caught by account graph.

## What survives Vanguard today (2026)

| Approach | Current lifespan | Notes |
|---|---|---|
| Internal usermode (DLL inject) | hours | Instant module-whitelist miss. |
| Manually-mapped usermode | hours–days | Allocation-signature scan catches it. |
| Manual-mapped kernel via BYOVD | days–weeks | Depends on which vuln driver; vulnerable drivers get added to Microsoft's block list within months. |
| Hypervisor (Type-1) | months+ | Boot-time EFI module above OS; Vanguard runs inside the guest. |
| **External vision + Arduino HID** | **invisible** | Zero code on gaming PC; behavioral ML can still flag superhuman tracking. |
| **DMA (PCILeech) + KMBox** | **invisible** | Zero code on gaming PC; Vanguard literally cannot see PCIe-side reads. |

## Re-derivation cadence

- **Offsets** — re-derive every patch (≈ every 2 weeks). Pointer chains shift.
- **BYOVD driver choice** — check Microsoft's driver block list monthly; swap when yours is added.
- **Behavioral tuning** — recheck accuracy distribution every season; Riot adjusts ML quarterly.
- **Hypervisor** — recompile after each major Windows cumulative update that touches hypervisor memory maps.

## Sources to re-read on patch day

- UC (UnknownCheats) Vanguard subforum — fresh-ban reports tell you which stack just died
- Riot's Security Engineering blog — occasional AC-change signposting
- Microsoft driver block list — https://aka.ms/VulnerableDrivers
