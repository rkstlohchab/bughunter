---
title: HWID spoofing — SMBIOS, disk, MAC, GPU UUID
tags: [hwid, spoofing, smbios, disk-serial, mac]
source: in-house
updated: 2026-11-12
lifespan_class: varies
---

# HWID spoofing

After a Vanguard / EAC / BE ban, your machine's hardware fingerprint is on the "no-serve" list. To play again with a new account, every item in that fingerprint must present a different value to the AC. Missing one chains the ban through.

## What AC sees

A conservative list of the fingerprint surface for a kernel-mode AC like Vanguard:

| Surface | Reader | Spoof depth |
|---|---|---|
| SMBIOS system UUID | `GetSystemFirmwareTable` | DMI table in RAM — kernel patch or BIOS mod |
| SMBIOS BIOS serial | SMBIOS table | Same |
| SMBIOS chassis serial | SMBIOS table | Same |
| SMBIOS baseboard serial | SMBIOS table | Same |
| Motherboard UUID | SMBIOS + `WMI Win32_ComputerSystemProduct` | Same |
| CPU serial / microcode | `cpuid eax=0x3` (deprecated, but microcode leaf is read) | Microcode revision spoof via MSR intercept |
| Disk serials (every drive) | `IOCTL_STORAGE_QUERY_PROPERTY` | Kernel SATA/NVMe driver patch |
| Disk model names | Same | Same |
| NIC MAC (every NIC) | `IoCtl` to NDIS | NDIS driver patch, or `netsh`/registry |
| GPU PCI device ID | `D3DKMTEnumAdapters2` | Not really spoofable; lie in AC's view |
| GPU UUID | `IDXGIAdapter::GetDesc` | Same |
| TPM EK pubkey hash (Win11) | TPM command | Can't spoof — swap TPM physically |
| Windows Install ID | Registry `MachineGuid` | `reg set` with elevated creds |
| Windows Product ID | Registry `ProductId` | Same |
| HDD partition GUIDs | Boot header | `bcdedit` / low-level partition tool |
| User SID | NTUSER.dat | New user account |

## Spoofer architecture

Three layers:

### Layer 1 — Registry/software-visible (easy)

- `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`
- `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProductId`
- `HKLM\SYSTEM\CurrentControlSet\Control\IDConfigDB`

Change via `reg.exe` with admin, reboot. Caught by any AC that reads via kernel API (Vanguard does — but registry-level is a baseline).

### Layer 2 — Kernel-visible SMBIOS/WMI (medium)

SMBIOS table is loaded into kernel RAM at boot. Patch the in-memory copy:
- Find the DMI table via `GetSystemFirmwareTable(RSMB)` to get format
- Write new values via kernel-write primitive (BYOVD) or your mapped driver

WMI queries land at `wmiprvse.exe` which reads SMBIOS + registry. Patched SMBIOS = patched WMI output.

Disk serials: hook the NVMe/SATA driver's `IOCTL_STORAGE_QUERY_PROPERTY` handler, return spoofed values.

NIC MAC: `netsh interface set interface mac=NEW` works for most NICs. For a driver AC reads via NDIS, hook NDIS at kernel level.

### Layer 3 — Firmware (hard, permanent)

Patch SMBIOS at the UEFI level. Edit the firmware image, re-flash. Survives Windows reinstalls, visible to every scanner.

Tools: `AMIBCP`, `AFUEFIx64`, vendor-specific flashers. Risk: brick. Use a flashback-capable motherboard.

## Known HWID spoofers (names only; verify current status)

- Public spoofers on UC with varying quality
- Private spoofers per-AC-target — last significantly longer
- Firmware spoof: one-off per motherboard

Public spoofers are themselves fingerprinted (allocation signatures, patched-value patterns). Vanguard catches the public ones.

## What actually needs spoofing per AC

### Vanguard (Valorant)

Everything. Vanguard's post-ban HWID list includes every row above except TPM. Firmware-level spoof + disk-serial spoof + NIC MAC + registry = clean boot.

### VAC + Trust Factor (CS2)

Partial. Steam's HWID is a loose hash of SMBIOS UUID + disk serial + MAC. Registry + MAC + one disk serial is often enough.

### BattlEye (R6, Tarkov)

Everything. BE is nearly as aggressive as Vanguard on HWID.

### EAC

Everything for Rust / Apex. Less for Fortnite / Elden Ring MP.

### BGMI / COD-M

Mobile IMEI + Android ID + build.prop fingerprint. Not a PC HWID problem — a mobile Magisk-hide + prop-spoof problem. See `stacks/mobile-root.md`.

## Validation flow

After a spoof, verify what AC sees before launching:

```powershell
# Check SMBIOS
Get-WmiObject Win32_BIOS | Select SerialNumber
Get-WmiObject Win32_ComputerSystemProduct | Select UUID
Get-WmiObject Win32_BaseBoard | Select SerialNumber

# Check MAC
Get-NetAdapter | Select Name, MacAddress

# Check disk serials
Get-PhysicalDisk | Select SerialNumber, Model

# Check Windows Install ID
reg query "HKLM\SOFTWARE\Microsoft\Cryptography" /v MachineGuid
```

Compare against your ban-window record.

## Second-PC path sidesteps all of this

If you run DMA or external vision, the gaming PC's HWID never ended up on a ban list — you never ran a cheat there. No spoof needed. This is the single biggest argument for the second-PC architecture.

## When HWID spoof is unavoidable

- Played a cheat previously on this rig and got banned
- Shared rig with someone who got banned
- Buying a used PC from unknown prior owner (may be pre-banned)

In those three cases, spoof before playing. In every other case, keep the rig clean and the cheat off-PC — no spoof needed.

## HWID spoofers in the GameLab adapter

`op_adapters/hwid_spoof.py` has a **read-only** reporting mode:
- Enumerate all HWID surfaces
- Compare against a baseline snapshot
- Flag any match with a known-banned fingerprint

Spoof writes themselves require a Windows guest + user-supplied spoofer (we don't ship a kernel-write primitive; too destructive-on-mistake). The adapter walks you through a spoofer run and verifies the result.
