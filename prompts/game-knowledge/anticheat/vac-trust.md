---
title: VAC + VAC Live + VACnet + Trust Factor (CS2)
tags: [vac, trust-factor, cs2, counter-strike, valve]
source: in-house
updated: 2026-11-12
lifespan_class: months
---

# Valve Anti-Cheat stack (CS2)

Valve's stack is less aggressive than Vanguard but has four distinct layers, each with its own ban semantics. Understanding which layer flagged you decides the response.

## Layer 1 — VAC (classic)

Signature-based scanner running in usermode inside the game process. Scans loaded modules and the game process's own memory for known cheat signatures. Bans are batched — a flagged match may sit for weeks before the ban lands in a "VAC wave."

- Low kernel presence. Not a boot-start driver.
- Signature updates pushed with the client; delayed days-weeks behind detection first-discovery.
- Immune to: unsigned-driver detection, kernel scans, module-walk hiding.
- Catches: any cheat that matches a known signature in game memory.

**Consequence:** permanent Steam account VAC ban on every VAC-secured game. Ban attaches to Steam ID, not HWID. Buying a new Steam account clears it.

## Layer 2 — VAC Live

Mid-match behavioral flagging. CS2 (unlike CS:GO) includes Live flagging that can kick a player from the match and session-ban. No permanent bans — but repeated Live kicks feed the VACnet model.

Signals:
- Snap-to-head pattern across multiple rounds
- Pre-fire ratio against known enemy positions
- Wallbang accuracy exceeding map-calculated pixel-walk probability
- Input-device hot-swap mid-match (plugged in a new "mouse" = Arduino) — rare flag but it exists

## Layer 3 — VACnet / Minerva (server-side ML)

Runs on Valve's servers, consumes GOTV demos of flagged matches. Features:

- Headshot distribution over a session
- Reaction-time histogram
- Flick accuracy by distance
- Pre-fire angles per map choke point
- Spray transfer curve (recoil-control automation signature)

VACnet output feeds both the VAC Live kick threshold and the Overwatch queue. Convictions from VACnet are typically batched into VAC bans (shown as "VAC ban").

## Layer 4 — Trust Factor + Overwatch

The silent killer. Trust Factor is a per-account score Valve does not publish. It correlates:

- Steam account age, purchase history, paid status (Prime)
- Reports received vs commends
- Shared hardware fingerprint with banned accounts (loose HWID — partial surface)
- Shared IP range with banned accounts
- Friends-graph edges to banned accounts
- Match performance variance vs account tenure
- Hours played on the game

Low Trust Factor matches you with other low-TF players (cheater lobbies) and makes Overwatch review more likely. Overwatch is community review of flagged demos — 11-of-15 or so human reviewers confirming a cheat triggers a ban.

A cheater with perfect behavioral opsec but low Trust Factor still loses — the matchmaker puts them in reviewer-dense lobbies.

## HWID surface (partial)

CS2 reads a subset of HWID at launch and includes hashes in the GOTV match header:

- Steam HWID (hashed SMBIOS + disk)
- GPU vendor/device ID
- Primary MAC
- Windows user SID

This is not a kernel-level HWID ban like Vanguard. Spoofing a VAC-banned machine to play again is straightforward — reinstall Windows plus any disk-serial spoof via a VM or hw-serial-spoofer. The weak spot is the Trust Factor's loose HWID clustering.

## Current lifespan matrix (2026)

| Approach | Lifespan |
|---|---|
| Internal usermode with known sig | days (wave ban) |
| Internal manual-mapped, custom code | weeks–months |
| External kernel driver | months |
| DMA FPGA | invisible to VAC; VACnet can still flag |
| External vision + HID | invisible to VAC; VACnet can still flag |

The real ban in CS2 is almost always behavioral, not signature. Build stacks accordingly.

## Re-derivation cadence

- **Offsets** — CS2 updates roughly weekly on Tuesdays; hazedumper on UnknownCheats or offsets repos rebuild same-day.
- **Signatures to avoid** — not published; keep code custom.
- **Trust Factor recovery** — only by time + legit games + commends; no fast path.

## Account opsec

- Use Prime status (requires phone + ≥ $14.99 game purchase) — nearly all cheaters are flagged into non-Prime first.
- Don't queue with low-TF or new accounts.
- Don't share IP with your main account.
- Don't accept friend requests from unknown Steam accounts — report-and-block is a TF hit.

## Sources

- https://github.com/frk1/hazedumper / https://github.com/a2x/cs2-dumper — live offset dumps
- UnknownCheats CS2 subforum — fresh VACnet patterns
- Valve's CS2 blog posts — occasional anticheat changelog
