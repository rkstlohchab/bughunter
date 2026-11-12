---
title: Kernel manual-mapper (BYOVD) stack
tags: [kernel, byovd, mapper, ring-0, windows]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Kernel manual-mapper — Bring Your Own Vulnerable Driver

The classic ring-0 cheat architecture. A signed driver with an arbitrary-kernel-read/write vulnerability (BYOVD) loads normally through the Windows driver subsystem. Your unsigned cheat driver is manual-mapped into kernel memory via that vuln. It then reads game memory from ring-0 — invisible to usermode scans and to `PsLoadedModuleList` walks.

## Why BYOVD

Modern Windows (HVCI / KMCI enabled) refuses to load unsigned kernel drivers. BYOVD is the workaround: load a signed-but-vulnerable driver, exploit its memory-read/write primitive to map your unsigned code into pool.

## Vulnerable drivers (2026, moving target)

Microsoft publishes a block list — `aka.ms/VulnerableDrivers` — of drivers the OS refuses to load when HVCI is on. The following were historically useful; verify status against the current block list before building against them:

| Driver | Vuln | Status |
|---|---|---|
| `gdrv.sys` (Gigabyte) | MSR R/W + PM | Blocked since 2022 |
| `RTCore64.sys` (MSI Afterburner) | MSR R/W | Blocked |
| `AsusCertService.sys` | arbitrary R/W | Blocked |
| `Capcom.sys` | arbitrary R/W | Blocked since 2016 |
| `WinRing0x64.sys` | MSR R/W + PM | Blocked |
| `ATSZIO64.sys` (Asus) | arbitrary R/W | Blocked |
| `truesight.sys` | read any memory | Blocked |

The arms race: new vulnerable drivers are discovered monthly (Loldrivers project tracks them). Microsoft adds to the block list on a lag.

## Current path (late 2026)

Current best approach:

1. Find a signed driver not yet on the block list. Loldrivers (https://www.loldrivers.io/) and `kdmapper`'s fork list carry active research.
2. If HVCI is enforced on the target rig, you need a driver that pre-dates Microsoft's block list enforcement — or you break HVCI with a VBS-disable bootkit. The latter breaks whole-system integrity and is overkill for most games.
3. Build your mapper on top of that driver's arbitrary-kernel-write primitive.

Alternative: **UEFI bootkit** that disables the block list before Windows loads (see `stacks/hypervisor.md`).

## Manual-map architecture

Your cheat driver is built as a standard PE with a `DriverEntry` that never gets called. The mapper does what Windows' `IoCreateDriver` does, by hand:

1. Allocate kernel pool (`ExAllocatePool` via vuln driver's write primitive)
2. Copy your PE's sections into the allocation (relocations, imports resolved by hand)
3. Jump to your `DriverEntry` from a kernel thread

Your driver is now running in ring-0 but has no entry in `PsLoadedModuleList`, no `DRIVER_OBJECT`, no dispatch table. That's the whole point.

## Hiding from AC scans

Techniques to add:

- **Big pool allocation** — avoid `ExAllocatePool2(POOL_FLAG_NON_PAGED, size, 'cheat')`. Use a pool tag that mimics a legitimate driver (`'ntoskrnl'` won't work but `'Twmi'` or similar legitimate tags blend in).
- **Allocation size obfuscation** — don't allocate exactly your image size; allocate larger and slice.
- **Page-table manipulation** — remap your allocation so its virtual address falls in a non-pool region, making `PsLoadedModuleList`-adjacent scans miss.
- **Hook-hiding** — use EPT (via hypervisor) to present different memory on read vs execute, so AC's memory scans see the original instructions while your hook executes.
- **Thread-stack cleanup** — detach the mapper thread so no stack frame points back to your code.

Without these, a kernel-mode AC scan that walks `MmNonPagedPoolStart..End` and checks for RWX regions catches you in minutes.

## Communication to usermode (overlay / aimbot)

Options for your overlay/aimbot process to talk to your kernel driver:

- **Device IOCTL** with a hidden device name (bad — easily enumerated)
- **Shared section** mapped into both kernel and your overlay process (better)
- **Callback hook** — register for an event (e.g. a timer) and your usermode process polls via a side channel (e.g. mouse HID writes with a magic value)

## AC detection techniques you must defeat

Per `anticheat/vanguard.md` kernel scans section — walk SSDT, walk dispatch tables, walk pool, walk PsLoadedModuleList, check page hashes. Each needs a defense:

| AC check | Your defense |
|---|---|
| `PsLoadedModuleList` walk | Mapped — no entry. Done. |
| Pool walk looking for RWX | Allocate RW, remap page tables on exec |
| Page hash mismatch | EPT shadow pages (requires HV) |
| Thread ID enumeration | Clean up mapper thread, inject at IRQL < DISPATCH via APC |
| Driver dispatch table scan | No `DRIVER_OBJECT` created. Done. |
| Suspicious imports | Resolve imports yourself via `MmGetSystemRoutineAddress` by hash |
| BYOVD driver fingerprint | Load the vuln driver with a randomized service name; delete service after load |

## Current realistic lifespan

Against Vanguard with HVCI on Windows 11:

- Freshly released BYOVD driver + custom mapper + EPT page hiding: **weeks to a few months**
- Published mapper (kdmapper, Drvmap, etc.) with a published vuln driver: **hours to days** — Vanguard's signature DB catches these fast
- Vanguard's HV-level checks (VMCS introspection, hypervisor-root detection) can detect naive hypervisors; a competent HV survives longer

Against BE, EAC: significantly longer. A clean mapper can run for months on these.

## Reference build

`cheats/kernel-mapper/`:
- `mapper.cpp` — manual-mapper against a Gigabyte-style arbitrary-write primitive (TEMPLATE; swap vuln driver)
- `driver_stub.cpp` — minimal kernel driver that reads game memory and shares via section
- `README.md` — full build + sign/load flow

## Setup runbook

`docs/game-lab/setup-kernel-mapper.md` — covers choosing a vuln driver, building the mapper, mapping, verifying the driver is running.

## When to pick DMA over kernel-mapper

| Criterion | Kernel-mapper | DMA |
|---|---|---|
| Upfront cost | $0 | $200+ (FPGA) |
| Learning curve | Medium-high (kernel debugging) | Low (Python) |
| Vanguard lifespan | Weeks | Invisible |
| Behavioral ML dodge | Your problem | Your problem |
| VBS/HVCI requirement | Must defeat | Doesn't care |
| Bricking risk | Yes (BSOD on mistake) | No |
| Game offset re-derive cadence | Per patch | Per patch |

If you have the budget, DMA wins. Kernel-mapper is still relevant for CS2 / BE titles where the arms race is slower, or when you want ring-0 for reasons beyond ESP/aim (e.g. network tapping for radar).
