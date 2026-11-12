---
title: Hypervisor-level bypass — below-OS strategies
tags: [hypervisor, hv, ept, type-1, uefi]
source: in-house
updated: 2026-11-12
lifespan_class: months
---

# Hypervisor-level bypass

See `stacks/hypervisor.md` for the full build. This file is about the bypass semantics — what you gain, and what you still have to defend against.

## What hypervisor-level buys you

- **Below ring 0.** No AC kernel driver can scan your HV root memory.
- **EPT page shadowing.** You can present different memory to the guest depending on access mode (read vs execute). AC reads "clean" pages; CPU executes your hooked pages.
- **MSR + CR trap.** Any attempt by AC to read special registers (SMBIOS via SMBUS, MSRs for hypervisor presence detection) can be intercepted and fed canned data.
- **CPUID spoofing.** Lie about VMX presence, hypervisor bit, vendor strings.
- **TSC offset.** Hide VM-exit timing by offsetting the TSC the guest sees.

## What it does not buy you

- **Behavioral ML on the cheat's outputs.** Flick curves, HS rate, reaction times — none of that goes away.
- **Server-side demo review.** VACnet runs on GOTV demos, not your hardware.
- **Account-graph linkage.** New account, same payment method = linked regardless.

## Architectural requirements

- Intel VT-x with EPT, or AMD-V with NPT (all consumer CPUs since ≈ 2010)
- UEFI (not legacy BIOS)
- Secure Boot off, or your HV signed with a db-enrolled key (requires db edit — uncommon on consumer setups)
- VBS/HVCI + your HV coexisting: either your HV runs first (boot-time .efi driver) or you nest

## Defending against AC HV detection

Vanguard and some BE deployments actively probe for an HV. Defenses, each required:

### CPUID spoofing

```
guest executes: cpuid eax=1
raw ecx[5] (VMX) = 1 (because the CPU supports it, visible to guest)
raw ecx[31] (hypervisor-present) = 0 or 1 (depends on BIOS)

On VM exit for CPUID, you intercept and:
  - clear ecx[31] if AC is testing it
  - clear ecx[5] if AC is checking VMX-availability
```

Check every leaf; some AC dig into `cpuid eax=0x40000000` (hypervisor vendor leaf) — must return "not present."

### MSR intercept

- `IA32_FEATURE_CONTROL` — lock bit set, VMX-outside-SMX disable bit set (so if AC tries to re-enter VMX from ring 0, it fails cleanly with #GP)
- `IA32_VMX_BASIC` and other VMX-specific MSRs — intercept read, inject #GP
- `IA32_DEBUGCTL` — don't intercept, pass through

### Instruction interceptions

- `vmcall`, `vmxon`, `vmxoff`, `vmread`, `vmwrite`, `invept`, `invvpid` — all raise #UD from guest ring 0, but if they weren't raising it (meaning you're there), AC infers HV. Intercept and inject #UD cleanly.

### TSC calibration attacks

AC measures `rdtscp` twice around an instruction that normally causes a VM exit (`cpuid eax=0`). If the delta exceeds a threshold, HV is detected.

Defense: on VM exits, apply a TSC offset to shrink the apparent gap. Needs calibration per-CPU-model.

### SMBIOS/ACPI table cleanup

AC reads SMBIOS vendor strings. On a bare-metal HV cheat, the SMBIOS already shows the real motherboard — no cleanup needed. But if AC checks ACPI tables you've modified (your HV added entries for debugging), restore originals.

### Memory map

Your HV steals memory from Windows. Change the UEFI memory map before Windows boots, mark your region as `MEMORY_RESERVED` — Windows won't see it, won't account for it. If AC checks total RAM vs SMBIOS-reported RAM, discrepancy = flag.

Reserve a small enough region (16-64 MB) that the diff is plausibly motherboard-reserved (GPU shared memory, etc.).

## Open-source references

- **HyperPlatform** (Satoshi Tanda) — educational, well-documented
- **HyperDbg** — advanced, used in research
- **Hypervisor-From-Scratch** (@SinaKarvandi series) — written tutorial
- **DayHV / HyperHide** — public cheat HVs, fingerprinted

Never ship a derivative of a public cheat HV as-is. Add your own instructions to the intercept stubs, randomize memory layouts, swap allocation patterns. Signatures of public HVs are maintained by AC vendors.

## Risks

- **UEFI corruption.** A bad write to flash during install bricks the board. Use a BIOS flashback-capable board for development.
- **Boot loop.** Bad HV init can prevent Windows boot entirely. Boot into UEFI shell from USB to disable your driver.
- **Triple fault = cold reset.** No BSOD, just reboot. Hard to debug.
- **PatchGuard clash.** Even if you don't touch kernel, Windows' own HV (Hyper-V) uses VMX. Clashing causes boot failure.

## Development workflow

1. Build HV as `.efi` driver
2. Sign with a self-signed key (or test with Secure Boot off)
3. Install into UEFI as a driver via `bcfg driver add`
4. Boot — your HV launches, Windows guest boots as usual
5. Attach WinDbg over net kernel debug from a second machine for ring-0 debugging (your HV's `vmcall`-based agent can forward)
6. Snapshot the UEFI config so you can disable your driver by boot-into-shell + `bcfg driver rm`

## When not to go HV

If you can afford DMA ($200), go DMA instead. Zero software on the gaming PC beats an HV in all the ways that matter:
- No HV build
- No bricking risk
- No per-CPU calibration
- No VBS clash
- No future-VT-d-enforcement risk is already paid down (DMA runs even if Vanguard enforces IOMMU on the CPU — the FPGA uses the IOMMU pass-through region for the game by default, or a BIOS-level IOMMU-off setting)

HV is a project for its own sake, or for a title where DMA doesn't fit (laptop, no PCIe slot).
