# Setup — Type-1 hypervisor

Below-OS cheat. Weeks-to-months lifespan vs even Vanguard. Significant
engineering + brick risk.

**Only attempt if you have a spare machine and WinDbg kernel-debugging
experience.** For a shipping cheat, DMA is usually the better choice —
see `setup-dma.md`.

See `cheats/hypervisor/README.md` for the full build plan and
`prompts/game-knowledge/stacks/hypervisor.md` for the theory.

## Prerequisites

- UEFI motherboard with a BIOS Flashback button (recovery for a bad HV)
- Intel VT-x + EPT, or AMD-V + NPT
- Secure Boot off (or self-signed db-enrolled key)
- A spare second machine for serial/net kernel debug

## Build order

1. Compose HyperPlatform / HyperDbg / Hypervisor-From-Scratch into a
   private HV. Don't ship a public fork — public HVs are fingerprinted.
2. Implement CPUID / MSR / vmcall spoofing (full checklist in
   `cheats/hypervisor/README.md`).
3. Implement EPT shadow pages for your hook targets.
4. Package as a UEFI driver (`.efi`).

## Install

As admin in Windows:

```powershell
bcdedit /copy "{current}" /d "GameLab HV"
bcfg driver add 00 FS0:\EFI\gamelab_hv.efi
```

Or from UEFI Shell:

```
bcfg driver add 00 fs0:\efi\gamelab_hv.efi "GameLab HV"
```

Reboot. Your HV takes VMX root; Windows boots inside as a guest.

## Verify

From the guest:

```powershell
# This must show VMX as NOT available (your HV spoofs it):
wmic cpu get VirtualizationFirmwareEnabled
# Expect FALSE even though the CPU supports it
```

If it shows TRUE, your CPUID spoof isn't complete — Vanguard would flag.

## WinDbg against the HV

Attach to the guest over net:

```
bcdedit /debug on
bcdedit /dbgsettings net hostip:<host> port:50000
```

Your HV's VMCALL back channel can relay messages to host WinDbg — see
HyperDbg's implementation for the pattern.

## Recovery

If Windows won't boot:
- UEFI Shell: `bcfg driver rm 0`
- BIOS Flashback button: re-flash original firmware

## Opsec defenses (mandatory)

Each of these must pass before trying a live match:

| Check | Ensure |
|---|---|
| `CPUID.1:ECX.VMX` | 0 in guest |
| `CPUID.1:ECX.31` | 0 in guest |
| `CPUID.40000000` | returns "not virtualized" |
| `IA32_FEATURE_CONTROL` | locked + vmx-outside-smx disabled |
| `vmxon / vmxoff / vmread / vmwrite / vmcall / invept / invvpid` | #UD from ring 0 |
| SMBIOS vendor strings | no Hyper-V/KVM/VMware traces |
| ACPI tables | consistent with bare metal |
| TSC on every VM exit | offset to hide VM-exit cost |
| UEFI memory map | reserved region matches SMBIOS total |

Miss any → detection.

## When DMA wins

- $200 vs weeks of build time
- No brick risk
- No per-CPU timing calibration
- No VBS clash
- No future-IOMMU-enforcement risk is paid down anyway

HV is a research project. For a daily cheat, go DMA.
