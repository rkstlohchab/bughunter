# Type-1 hypervisor cheat — build plan

The cheat lives in VMX root mode. Windows + the game + the anticheat run as
a guest. Vanguard (and any kernel AC) can scan ring 0 all it wants —
they're inside your guest.

See `prompts/game-knowledge/stacks/hypervisor.md` for the theory and
`prompts/game-knowledge/bypass/hypervisor-level.md` for AC detection
defenses.

This directory does NOT ship a working HV source tree. HV code is highly
destructive-on-mistake (brick, triple fault, UEFI corruption), and public
HV cheats are already fingerprinted. Instead this README lays out the
build plan + reference projects you compose into your own private HV.

## Hardware prerequisites

- UEFI motherboard with a BIOS Flashback button (recovery path for a bad
  HV install)
- Intel VT-x + EPT, or AMD-V + NPT (every consumer CPU since 2012)
- Secure Boot off, or your HV signed with a db-enrolled key
- A second machine for serial/net kernel debugging

## Reference projects — read and compose, don't fork

| Project | Use for |
|---|---|
| HyperPlatform (Satoshi Tanda) | Clean VMX enter/exit scaffolding + VMCS fields |
| HyperDbg | Modern VMX + EPT shadowing + debugger surface |
| Hypervisor-From-Scratch (SinaKarvandi) | Written tutorial, line-by-line |
| STM / HyperV-Backdoors research papers | Nested-virt tricks |
| DayHV / HyperHide (public cheat HVs) | **fingerprinted — study only** |

## Build order

1. **Boot-time .efi driver** — register via `bcfg driver add`, run at
   `BeforeExitBootServices`. Sets VMX up BSP + APs, then hands off to
   bootmgr.efi as if you were never there.
2. **VMCS setup** — fill in host state / guest state / execution controls.
   VM exits for CPUID (spoof), MSR access (filtered via MSR bitmap),
   control-register writes, EPT violations.
3. **EPT shadow** — for pages you want to hide, use independent R-EPTE and
   X-EPTE via the EPT_VIOLATION handler. Read traffic maps to original
   bytes; execute traffic maps to your hooked bytes. KPP-safe because KPP
   doesn't walk EPT.
4. **CPUID / MSR spoof** — every leaf AC checks (vendor, hypervisor-present
   bit 31, VMX-support bit 5, hv-vendor leaf 0x40000000) rewritten in the
   VM-exit handler.
5. **TSC offset** — subtract the measured VM-exit latency off `IA32_TSC`
   before returning to guest. Prevents timing-attack detection.
6. **Cheat payload** — your ring-0 cheat code runs in VMX root context,
   reads game memory via the guest's PML4 (translate guest-virtual via
   EPT), publishes to a kernel page mapped into usermode via EPT also.
7. **VMCALL back-channel** — overlay issues VMCALL with a magic ID in
   `rax`; HV catches it before injecting #UD. One VMCALL per action
   (get_entities, get_view_matrix, ...).

## AC-defense checklist

Every item below is independently checked by Vanguard and must pass:

- [ ] `CPUID.1:ECX.VMX` (bit 5) spoof
- [ ] `CPUID.1:ECX.31` (hypervisor-present) spoof
- [ ] `CPUID.40000000` (hv-vendor leaf) returns "no HV"
- [ ] `IA32_FEATURE_CONTROL` returns locked + vmx-outside-smx disabled
- [ ] `vmxon`, `vmxoff`, `vmread`, `vmwrite`, `vmcall`, `invept`, `invvpid`
      from guest ring 0 inject #UD cleanly (as if VMX unsupported)
- [ ] SMBIOS tables scrubbed of vendor strings mentioning Hyper-V/KVM/VMware
- [ ] ACPI tables consistent with bare metal
- [ ] TSC offset on every VM exit
- [ ] UEFI memory map marks your reserved region so Windows's SMBIOS
      memory total matches the physical total

## Install

```powershell
# Sign your .efi driver with your self-signed key
# (in test-signing mode, or with db-enrolled cert on Secure Boot)

# As admin, from Windows:
bcdedit /copy "{current}" /d "GameLab HV"
bcdedit /set "{HV GUID}" bootsequence "{HV GUID}"
bcfg driver add 00 FS0:\EFI\gamelab_hv.efi

# OR from UEFI Shell:
bcfg driver add 00 fs0:\efi\gamelab_hv.efi "GameLab HV"
bcfg driver dump

# Reboot. HV takes VMX root; Windows boots inside.
# Verify from guest (should FAIL — HV spoofs it):
# cmd> wmic cpu get VirtualizationFirmwareEnabled
```

## Recovery

If Windows won't boot:
- From UEFI Shell: `bcfg driver rm 0`
- From BIOS Flashback: re-flash original firmware

## Why DMA is usually better

- $200 FPGA vs weeks of HV dev
- No brick risk
- No per-CPU timing calibration
- Zero software on the gaming PC
- No VBS clash

Build the HV as a research project, not a daily driver. For a shipped
cheat, go DMA.
