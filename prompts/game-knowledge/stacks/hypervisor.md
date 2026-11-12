---
title: Type-1 hypervisor cheats (below the OS)
tags: [hypervisor, type-1, hv, uefi, ept, ring-neg-1]
source: in-house
updated: 2026-11-12
lifespan_class: months
---

# Type-1 hypervisor — the cheat lives below the OS

A UEFI driver or boot-loaded hypervisor initialized before Windows starts. It installs VMX root mode on the CPU, launches the regular Windows kernel as a guest, and keeps the cheat code in VMX root memory, invisible to anything the guest OS (and its kernel-mode anticheat) can see. The AC runs inside your hypervisor.

Below ring 0. Also called "ring -1."

## Why

Vanguard boots as a ring-0 driver. A ring-0 driver cannot inspect VMX root mode. If your cheat lives in VMX root and uses EPT (Extended Page Tables) to shadow the game's memory pages, Vanguard sees a normal system from inside the guest. Every scan it runs is a scan of a guest view you control.

Requires:
- Intel VT-x + EPT, or AMD-V + NPT (every CPU in the last 12 years)
- UEFI (you can't do this with legacy BIOS)
- Secure Boot off — or your HV signed by a key enrolled in db

## Architecture

```
┌───────────────────────────────────┐
│ Windows + game + Vanguard         │  ← guest, ring 0 / ring 3
│                                   │
│ (sees "normal" hardware)          │
└──────────────┬────────────────────┘
               │  VM exits
               v
┌───────────────────────────────────┐
│ Your hypervisor (VMX root)        │  ← ring -1
│  - EPT violation handler          │
│  - MSR intercept                  │
│  - CPUID spoofing                 │
│  - Cheat read/write routines      │
└───────────────────────────────────┘
               │
┌──────────────┴────────────────────┐
│ Physical hardware                  │
└───────────────────────────────────┘
```

## Core primitives

- **EPT shadow pages** — for memory reads, present an "innocent" page to the guest's memory scanner; for memory execution, present the actual (hooked) page.
- **MSR bitmap** — intercept writes to `IA32_SYSENTER_EIP` etc. that might disable your hooks.
- **CPUID vmx-leaf spoof** — return "not virtualized" for `CPUID.1:ECX.VMX`. (Vanguard checks this.)
- **VMCALL back channel** — your usermode overlay issues `vmcall` with a secret ID; the HV catches it and processes a cheat command.
- **Nested-virt trap** — if the guest tries to install its own hypervisor (some AC do this to detect you), either hide it or refuse.

## Key defenses against AC HV-detection

Vanguard specifically probes for a hypervisor. You must defeat each:

| AC check | Defense |
|---|---|
| `CPUID.1:ECX.VMX` reads 1 | Spoof to 0 in VMX root |
| `rdmsr(IA32_FEATURE_CONTROL)` | Spoof, or intercept + return locked |
| Hypervisor-present CPUID bit (`CPUID.1:ECX.31`) | Spoof to 0 |
| `vmcall` from ring 0 — illegal in non-root; fault pattern | Intercept the fault, inject a convincing #UD |
| Timing checks (`rdtscp` delta before/after an instruction known to cause VM exit) | Fudge TSC on VM exits — spoof TSC with an offset |
| MP table / ACPI table vendor strings mentioning Hyper-V, KVM, VMware | Clean the tables presented to the guest |
| VMCS introspection — Vanguard may try to `vmread` from ring 0 | Install `#UD` handler for all VMX instructions in guest |

Any missed signature = detection.

## Boot flow

1. UEFI firmware loads your signed `.efi` (either via BootServices hook or installed as a driver)
2. Your HV sets up VPIDs, EPT, VMCS for BSP (bootstrap CPU)
3. Each AP (application processor) IPI'd into VMX root mode
4. Guest launches — hands off to `bootmgr.efi` as if HV wasn't there
5. Windows boots, loads `vgk.sys`, Valorant starts — all in guest

## Reality check

- Building a production HV cheat takes weeks for a decent kernel engineer, months for a learner. You're building hyper-v-lite with defenses against detection.
- Hyper-V, VBS, HVCI on the gaming PC fight for VMX root — you'll clash. You run first (boot-time) and refuse the guest's own HV requests, or you leave VBS enabled and nest. Nested-HV detection by AC is weak but nonzero.
- Memory bugs here cause black screens, triple-faults, UEFI bricks. **Test on a spare machine.**
- Open-source references: HyperPlatform, HyperDbg, Hypervisor-From-Scratch, Hyper-V-Backdoors, STM. All educational.

## Upgrade: VBS bypass

To run on a Windows 11 rig with VBS/HVCI enabled, your HV needs to either:

- Load before VBS (as a UEFI driver registered to run `BeforeExitBootServices`), then disable VBS's HV launch, or
- Nest under VBS (take VMX root while VBS has already taken it) — hard but documented in STM papers

The alternative is to disable VBS on the target rig — but a VBS-off Windows 11 is itself a signal to Vanguard (it wants VBS on). Trade-offs either way.

## Current lifespan

- Clean, non-public HV with full CPUID/MSR spoof + EPT shadow: **months**
- Based on a public project (HyperPlatform-derived): **weeks**
- Public HV that ships with many cheat loaders (`DayHV`, `HyperHide` fork-descendants): **hours to days** — fingerprinted

## When this is overkill

- CS2 / BE / EAC don't currently run HV-detection seriously. A kernel-mapper is enough.
- Vanguard, maybe. Many top Valorant cheats run on DMA instead — zero software on the gaming PC beats an HV in both cost and risk.

## Reference

- `cheats/hypervisor/README.md` — overview + build plan
- No full HV source in-repo (too destructive if misbuilt). Build from HyperPlatform + custom detection defenses.
