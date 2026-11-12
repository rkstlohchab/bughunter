---
title: OS-level bypass — Windows hardening the cheat hides behind
tags: [bypass, os, windows, hwid, host]
source: in-house
updated: 2026-11-12
lifespan_class: varies
---

# OS-level bypass

Techniques at the OS layer that reduce the anticheat's visibility into your rig. Pair with a stack from `stacks/`.

## Clean Windows install

Always start here. A reused Windows install carries:
- Windows Installation ID (hashed into VAC Trust Factor and EAC HWID)
- SRUM database with months of process + network activity
- Prefetch files for any cheat you ran before
- Event Log entries of your prior ban
- Services registered for any prior cheat loader

For a long-lifespan rig:
1. Wipe drive (zero overwrite, not just quick format — SSDs: `nvme format` on Linux, or vendor tool)
2. Fresh Windows 11 install from USB
3. Skip MSA signin — use local account
4. Disable Windows Hello, telemetry via Group Policy
5. Keep Defender enabled (disabling Defender is a Vanguard flag)

## User profile isolation

Each game runs under its own Windows user. Prevents accidental cross-contamination:
- Game 1: `user_valorant` → runs only Valorant + cheat A
- Game 2: `user_cs2` → runs only CS2 + cheat B

User SIDs are different. Vanguard/BE/EAC that correlate by SID can't link two accounts.

## Steam / Riot client isolation

Use separate installs per account:
- Steam: `C:\Steam\account1\` vs `C:\Steam\account2\` — move install between sessions, or use Steam Library folders
- Riot: separate install directories via portable Riot Client forks

Payment instruments: different cards or crypto-funded virtual cards per account.

## IP isolation

- VPN per account (or VPS-tunneled WireGuard)
- Different VPN vendor per account (region-match to account registration country)
- Never queue two accounts from the same egress IP
- Residential proxy if VPN detection becomes a flag (Vanguard increasingly datacenter-IP shadowbans)

## Network stack

- Block anticheat telemetry? **Don't.** Vanguard refuses to play if `riot1.net`, `riot2.net` are unreachable. Same for BattlEye, EAC. Telemetry blocking = ban.
- Mitigate side channels: no VOIP app hooks into the game process (Discord in-game overlay is fine; some third-party voice packs leak audio headers).

## Anti-forensics on ban

If a ban happens, your rig has forensic residue:
- Prefetch under `C:\Windows\Prefetch\` for every binary run
- SRUM under `C:\Windows\System32\sru\SRUDB.dat`
- SetupAPI logs under `C:\Windows\INF\setupapi.dev.log` for loaded drivers
- Event Log `Application.evtx`, `System.evtx` for driver loads, service starts
- ShellBags, UserAssist, Jump Lists for folder access

Clean before re-install, or just wipe. If swapping a drive's contents into a new account, every one of these entries carries the fingerprint.

## Secure Boot and HVCI

On Windows 11 with VBS/HVCI enabled:
- Can't load unsigned drivers (your mapper needs BYOVD-signed start, then unsigned map)
- Hypervisor-root is taken by Windows own Hyper-V — nested-HV cheat paths get harder
- But: Vanguard prefers VBS on. If you disable VBS, you *stand out* among honest players.

Most pros of Vanguard cheats build their mapper to work with VBS/HVCI enabled, not against it.

## Timing/perf artifacts

Some AC check for VM-like timing. If you run your cheat inside a VM on the gaming PC (why would you), timing is a dead giveaway. Second-PC + HID-mouse avoids every one of these.

## Windows Update discipline

- Keep OS patched. Out-of-date Windows is a Trust Factor hit on CS2.
- Delay new Windows feature updates by 1-2 weeks — each has broken known kernel-mapper vuln-driver loading in the past.

## Driver signing

- Keep testsigning OFF. `bcdedit /set testsigning on` is a Vanguard flag.
- `nointegritychecks` off.
- Don't boot into "Disable Driver Signature Enforcement" mode to load unsigned drivers; it's a session flag Vanguard logs.

## Bottom line

Fresh install, local account, per-game user, VPN per account, different HWID (next doc), every game's install isolated, don't block telemetry, don't flip integrity flags. Everything else is gravy.
