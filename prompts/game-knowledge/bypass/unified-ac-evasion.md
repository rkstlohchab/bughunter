---
title: Cross-game AC evasion — the universal pattern catalog
tags: [evasion, bypass, cross-game, humanize, anti-detection]
source: in-house
updated: 2026-11-12
lifespan_class: always
---

# Cross-game AC evasion catalog

The techniques below apply to every target game — Vanguard, VAC+Trust,
BattlEye, EAC, Ricochet, bgmi-ace, Garena shield. Mix and match based
on the per-AC teardowns in `anticheat/*.md`.

## Signal surface map

Every AC checks some subset of:

| Signal | What catches you | Mitigation |
|---|---|---|
| Module whitelist | internal usermode DLL | manual-map; no `LoadLibrary` |
| Allocation signature | known mapper bytes in pool | pool tag disguise; big-pool evasion |
| Bundle / image hash | modified binary (iOS, mobile) | hash hook; never share your IPA |
| Loaded image enumeration | your dylib visible to dyld | fishhook `_dyld_*` to filter |
| Handle enumeration | open PROCESS_VM_READ to game | DMA (no handle) |
| Driver dispatch walk | your kernel `DRIVER_OBJECT` | manual-map without IoCreateDriver |
| `PsLoadedModuleList` walk | your sys in the list | mapped — no entry |
| SSDT/IDT integrity | your kernel hooks | hook via EPT shadow (needs HV) |
| Thread stack trace | kernel thread points to your code | APC context, not dedicated thread |
| Hyper-call probe | CPUID VMX bit, MSR intercept | HV spoofs CPUID+MSR for guest |
| Screenshot compare | your in-process overlay | external overlay only |
| Timing (`rdtscp`, `GetTickCount`) | VM-exit latency, hook latency | TSC offset (HV); defer init |
| Network telemetry allowlist | you blocked anogs/vgc traffic | DON'T block — detected instantly |
| Behavioral ML | superhuman aim, flick, HS% | humanize (see `opsec/behavioral.md`) |
| HWID chain | banned machine fingerprint | spoof every HWID surface |
| Account graph | shared payment/email/IP | isolate per account |

## The five escalating stacks

Pick the lowest stack that gives your target game's AC enough runway:

### Stack 1 — External only (invisible)
- No code on the gaming device.
- Vision (capture + YOLO) or DMA (PCILeech + MemProcFS).
- Behavioral ML on account is the only remaining signal.
- Lifespan: account lifetime, if humanization is correct.

### Stack 2 — Manual-mapped usermode
- DLL mapped into game process without `LoadLibrary`.
- Defeat module whitelist. Still signature-scannable.
- Lifespan: hours on VAC (sig scan), days on BE/EAC, hours on Vanguard.

### Stack 3 — Kernel mapper (BYOVD)
- Signed vuln driver loads; your unsigned driver manual-mapped into
  pool without `IoCreateDriver`.
- Hiding techniques: pool tag disguise, big-pool evasion, page-table
  remap, EPT shadow (if paired with HV), thread stack hygiene, hashed
  imports.
- Lifespan: days on Vanguard (with EPT), weeks on BE/EAC with hygiene.

### Stack 4 — Hypervisor (Type-1)
- Boot-time UEFI `.efi` driver puts CPU into VMX root; Windows + AC
  run as guest.
- Full CPUID/MSR spoof + EPT shadow + TSC offset.
- Lifespan: months on Vanguard with a non-public HV.

### Stack 5 — Mobile dylib / Zygisk
- Injection into the game process itself via Magisk+Zygisk (Android)
  or patched-IPA+AltStore (iOS).
- Combines bundle-hash hook + dyld hide + tamper hook.
- Lifespan: 1-4 weeks on BGMI/COD-M/FF between anogs updates.

## Universal hiding patterns

### Pattern — allocation hygiene

Every allocation your cheat makes is a signal. Rules:
- **Tag mimicry.** `ExAllocatePool2(POOL_FLAG_NON_PAGED, size, 'MmDl')`
  — not `'cht '`. Pick a tag the kernel already uses heavily.
- **Size masking.** Don't allocate exactly your image size. Pad to a
  common size class (`PAGE_SIZE` multiple with a small offset).
- **Big-pool escape.** Large allocations go into `RtlpBigPoolTable`
  which AC enumerates. Keep allocations under `PAGE_SIZE`, chain
  smaller blocks.
- **Lifecycle.** Free + reallocate periodically to randomize the
  address. A scan that cached your address between ticks misses.

### Pattern — hash-based import resolution

Don't use `GetProcAddress` / `MmGetSystemRoutineAddress` by name —
it's instrumented. Resolve by hash:

```c
#define HASH_ExAllocatePool2  0x4FE6A54C
typedef PVOID(*pAlloc)(POOL_FLAGS, SIZE_T, ULONG);
pAlloc Alloc = (pAlloc)resolve_by_hash(knt_base, HASH_ExAllocatePool2);
```

Walk the kernel's export table yourself; hash each name; compare.
No string ever touches memory.

### Pattern — hook relocation

A hook at a fixed call site is a single-point-of-failure. Instead:
- **Mid-function hook.** Pick an instruction several bytes into the
  function; harder for AC to detect than a `jmp` at `+0`.
- **IAT hook over inline hook.** For usermode: patch the Import Address
  Table instead of the first instruction.
- **Rotating hooks.** Install the hook, run for N seconds, remove and
  reinstall at a different offset within the target function.

### Pattern — self-hash shadow

If AC hashes its own code pages:
- **EPT shadow.** Two EPT entries for the same virtual address — one
  for reads (original bytes), one for executes (hooked bytes). AC sees
  original on hash; CPU runs hooked.
- **Page-fault handler.** If no HV: install a `#PF` handler that
  returns the original byte on read-access, actual byte on
  instruction-fetch access. Requires modifying kernel's `IDT[0xE]`
  entry — HVCI-blocked without BYOVD.

### Pattern — timing jitter

Every scan has a time budget. Add latency to things you want hidden,
not to the whole process (which flags timing-based detection):

- **Deferred init.** Load the dylib/driver, wait 5-9s before
  installing hooks. Launch-window scans run clean.
- **Jittered polling.** If you read game memory every 10ms, that's a
  fingerprint. Random distribution 5-15ms is not.

## Behavioral evasion — unified humanization

See `opsec/behavioral.md` for per-game floors. The universal shape:

```python
# Reaction floor — log-normal, 150ms minimum
reaction_ms = max(150, int(np.random.lognormal(np.log(220), 0.28)))

# Flick curve — quadratic ease-in-out
def ease(t): return t * t * (3.0 - 2.0 * t)

# Miss rate — randomly pushes you off-target
if rng.random() < miss_rate: dx += rng.uniform(-14, 14)

# Deadzone — stop correcting when crosshair is on-target
if abs(dx) < 3 and abs(dy) < 3: return

# Tick gate — max ~11 corrections/sec
if (now - last_tick) * 1000 < 90: return

# Pre-fire delay — don't click before you've aimed
if time_since_acquire < 3_frames: no_shot
```

Add per-game recipe:
- **Valorant**: reaction 180ms, HS% cap 48, miss 0.08, pre-fire 15%
- **CS2**: reaction 160ms, HS% cap 50, miss 0.08, spray transfer 0.68
- **BGMI/mobile**: reaction 220ms (phone players are slower), miss
  0.12, no trigger (touch tier)
- **Apex**: reaction 170ms, HS% cap 40, miss 0.09
- **Among Us**: not an aim game; focus on no-cooldown + wallhack

## Account graph isolation

See `opsec/account-hygiene.md`. The universal rules:

- **Fresh email per account.** ProtonMail free, not Gmail alias.
- **Fresh phone/SMS for 2FA.** 5sim.net, SMSArena.
- **Fresh payment.** Privacy.com (US), crypto-funded virtual cards
  (TruBit, BitPay) elsewhere.
- **Fresh IP per account.** Residential proxy if running 2+. Never
  share a /24 between accounts.
- **No OAuth cross-link.** Don't link Twitch/Discord/BN/Game Center
  to a cheating account.
- **Friends graph discipline.** Don't queue with low-trust accounts
  on CS2; don't accept random invites on BGMI.

## Match-pace discipline

See `opsec/match-pacing.md`:
- Climb at ≤2 ranks/month after placement
- Winrate 55-62% over rolling 50-game windows
- HS% within the 95th percentile of your rank
- Don't stream the cheating account
- Don't post montages
- Play silent

## Red-flag early warnings

Your account is about to be banned when:
- CS2: Trust Factor drops silently (new matches put you in cheater
  lobbies → every opponent is bot-like)
- Valorant: match quality degrades (long queues, below-rank opponents)
- BGMI: TDM/ranked suddenly filled with other obvious cheaters (bot
  lobby queue)
- All: a chat/console warning from the AC

Any of these → stop the session, spoof HWID (or re-sideload on iOS),
rotate IP, wait a week, resume with lower miss-rate humanization.

## Emergency: account went down

1. Note which hardware, IP, payment, email the account used.
2. Assume all four are now graph-flagged.
3. Full rotation: new HWID spoof + new IP + new payment + new email
   before the next account.
4. Don't re-login on the banned account to "check" — that confirms +
   timestamps the linkage.

## What survives the longest, per target (2026 snapshot)

| Target | Best stack | Realistic lifespan |
|---|---|---|
| CS2 ranked | DMA + humanization + Trust Factor opsec | months |
| Valorant Immortal+ | DMA + HV (when behavioral ML flags) | months |
| Apex Preds | DMA + careful HS% + session variance | weeks-months |
| Rust server with HWID | DMA + HWID spoof + fresh account | weeks |
| BGMI iOS competitive | patched IPA + full anti-detect suite + behavioral | 2-4 weeks |
| COD MW/WZ | DMA + HV + strict humanization | weeks |
| Fortnite / BattleEye | DMA + humanization | weeks-months |
| **Among Us (local)** | **memory writes, no AC** | **indefinite** |
| **Among Us (online)** | **client-side cheats only, server validates** | **weeks** |
| Free Fire | mobile dylib + heavy humanization | days-weeks |
