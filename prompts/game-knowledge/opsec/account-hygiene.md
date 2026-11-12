---
title: Account hygiene — IP, payment, email, friends graph
tags: [opsec, account, hygiene, ip, payment]
source: in-house
updated: 2026-11-12
lifespan_class: always
---

# Account hygiene

An unbanned rig running a clean cheat still loses the account to graph linkage. AC vendors cluster accounts by shared edges; a new account on a known-banned graph is banned by association.

## IP isolation

- One residential IP per cheating account. Different from your main's IP by at least /24, preferably different ISP.
- VPN per account — different vendor, different exit, different country sometimes. Mullvad / IVPN pay in crypto = no payment-linked.
- Datacenter IPs are increasingly shadowbanned. Vanguard and Riot rank-protection detect top VPN ranges and funnel accounts into cheater lobbies. Residential or residential-proxy is the floor.
- Never queue two accounts from the same egress IP in a 24h window.

Residential proxy services (Oxylabs, Bright Data, SOAX) have pricing from ~$15/GB. Plenty for gameplay bandwidth. The proxy must support UDP for voice + match traffic (many don't — check).

## Payment instruments

- Each account gets its own payment:
  - Prepaid cards (Netspend, Bluebird, Chime)
  - Privacy.com virtual cards (US only)
  - Crypto-funded virtual cards (BitPay, TruBit)
  - Gift cards for store purchases (Steam, Riot, PUBG UC codes bought from reseller)
- Never pay for two accounts with the same card.
- Never link a PayPal that has ever been linked to a banned account.
- Google Play / App Store: fresh Google / Apple ID per phone profile; billing region matches VPN region.

## Email

- Fresh email per account. Not a Gmail-alias trick — a completely separate inbox.
- Options:
  - ProtonMail (free tier, no phone needed)
  - SimpleLogin or AnonAddy aliases if the game accepts them (Riot increasingly blocks)
  - Burner email (SMSArena, etc.) for 2FA where phone isn't required
- Recovery email set to another burner, not your main's.

## Phone

- Game-side 2FA phone: use a dedicated SIM or a phone-verification service (e.g. 5sim.net, SMSArena).
- Not your main number. Not a Google Voice tied to your main.
- Keep records per account of which phone/burner is tied to which so you don't lose access.

## Friends graph

- Don't friend accounts you don't have long legitimate history with.
- Don't queue with a known-cheater or a recently-banned account in CS2 — Trust Factor hit.
- Don't accept random friend requests — reports/commends matter.
- Riot ignores friends-graph less than Steam does; Steam ignores it less than mobile games do.

## OAuth / third-party linkage

- Twitch, Discord, Battlenet cross-links are a graph edge. New account on Riot + linked to your main's Twitch = linked.
- Keep per-account Twitch/Discord/BN if you need them, or don't link at all.

## Device rotation

- If you have two accounts on the same rig, log fully out between them. Clear:
  - Steam: delete `ssfn*` + `config.vdf` + `loginusers.vdf`
  - Riot: delete `%LOCALAPPDATA%\Riot Games\Riot Client\Data`
  - Game-local: `%LOCALAPPDATA%\VALORANT`, `~\Documents\Rainbow Six - Siege`, etc.
- Spoofed HWID switch if sharing a rig (see `bypass/hwid-spoofing.md`).

## Streaming / social

- Don't stream a cheating account. OBS captures include memory addresses visible in-game (killcam positions, damage numbers) that confirm cheats to a human reviewer.
- Don't post montages that include killcam HUDs.
- Don't boast on Discord in an audible room.

## Operational logs

- Keep a per-account log:
  - Email + password (in a password manager, not plain text)
  - Phone/2FA used
  - Payment instrument
  - VPN endpoint range
  - Date created, hours played, current rank
  - Any flags received (chat warnings, kick, review notice)
- `assets.query` in GameLab stores these as `credential` + `host` + `loot` entries. Keep structured.

## Bans propagate; prepare

If an account goes down:
- Note which hardware fingerprint it was on
- Note the payment and email used
- Burn all three — new fingerprint spoof, new payment, new email for the next account
- Don't re-login with the banned account to "see" — that confirms the ban + attaches a timestamp that may update linkage

## Red flags that mean pause

- Friend getting chat warnings from the AC
- "Trust Factor dropped" notice (CS2) — your Trust Factor is tanking regardless of visible bans
- Matchmaking suddenly all against suspicious players (cheater pool indicator)
- Match quality downgrades silently (BGMI emulator-pool quietly)

Any of these = pause the account, spoof HWID, change IP, wait a week. If the next session shows the same pattern, burn the account.

## Checklist — new account spin-up

```
[ ] Fresh email (no alias)
[ ] Fresh phone / burner number
[ ] Fresh payment (prepaid or crypto virtual card)
[ ] New IP (different /24 from main)
[ ] No OAuth links
[ ] No friends-graph edges to main
[ ] HWID spoof verified (`gamelab.hwid_check`)
[ ] Behavioral floors loaded for this game
[ ] Logged in GameLab assets with per-field metadata
```
