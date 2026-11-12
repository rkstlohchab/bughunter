---
title: Tencent ACE / TGPA — PC emulator anticheat
tags: [tencent, ace, tgpa, gameloop, cf, delta-force]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Tencent ACE / TGPA

Tencent Anti-Cheat Expert (ACE) on PC + Tencent Gaming Protection Agent (TGPA) in GameLoop. Powers CrossFire, Call of Duty Mobile on GameLoop, BGMI on GameLoop, Delta Force (Tencent build), Honor of Kings PC emulator path. Shares code with mobile ACE but adds PC-specific scans.

## Components

- `TenSafe.exe` / `ACE-BASE.exe` — usermode guard process.
- `ACE-GUARD.sys` — kernel driver when installed on bare metal.
- `TsCometEngine.dll` — injected into the game process.

In GameLoop, TGPA runs inside the Android VM alongside the game — but uses PC-specific hooks to monitor the host for process injection, debugger attachments, and memory-read tools.

## Scans

- Classic module whitelist + signature scan inside game process
- Kernel callback registration (when kernel-mode ACE is loaded)
- Debugger detection via PEB.BeingDebugged, NtQueryInformationProcess, timing checks
- Process enumeration — any unknown process with a handle to the game = flag
- VM detection (ironically, since TGPA itself runs in a VM) — detects nested virtualization
- GameLoop-specific: watches for Android debug bridge (`adb`) connections, Frida server on the VM, etc.

## Behavioral layer

- Shared with mobile ACE — recoil-pattern ML, HS distribution, aim-trajectory smoothness
- Account trust score (undocumented) decides lobby matching

## Ban types

- Match-session kick → short cooldown (hours to days)
- Account suspension → 1 week - 30 days - permanent progression
- HWID ban on repeat offenders (Tencent uses SMBIOS + disk + GameLoop install UUID)
- Payment-method ban (any VPay/WePay/UPI account linked to banned profile)

## Bypass viability

Same stack hierarchy as other kernel-ACs:

| Approach | Lifespan |
|---|---|
| Internal usermode on GameLoop | hours |
| Manual-map or userland hiding | days |
| **GameLoop + external overlay via OBS virtual camera → YOLO → ADB input** | weeks |
| **Real DMA (if ACE loaded kernel-mode on bare metal)** | months |

## HWID surface

- SMBIOS UUID
- Disk serial
- NIC MAC
- GameLoop install UUID
- Wegame account ID (if used)

## Re-derivation

- Per game update, often weekly for CrossFire
- TsCometEngine signature list updates monthly

## Sources

- UnknownCheats CrossFire / Delta Force / HoK PC subforums
- Chinese forums (bbs.52pojie.cn) carry ahead-of-English-market Tencent ACE research
