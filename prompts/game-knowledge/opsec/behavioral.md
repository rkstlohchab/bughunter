---
title: Behavioral opsec — humanization floors for cheats
tags: [opsec, behavioral, humanize, aimbot, esp]
source: in-house
updated: 2026-11-12
lifespan_class: always
---

# Behavioral opsec

Even a hardware-perfect, AC-invisible cheat loses to behavioral ML. Vanguard's model is tuned on the top 0.5% of player statistics; Trust Factor + VACnet clusters outliers; BGMI anogs feeds a similar model. These rules keep you inside the "fits distribution" envelope.

## Hard floors — never cross

| Metric | Floor | Why |
|---|---|---|
| Reaction time (enemy pixel-visible → first move) | ≥ 150ms | Humans sit at 180-240ms |
| Flick arc | Quadratic bezier (ease-in-out) | Humans don't teleport cursor |
| Miss rate | ≥ 0.05 | 100% hits = 100% flag |
| Max corrections / sec | ≤ 11 (90ms tick) | Real mouse input is bursty, not continuous |
| Dead zone around target | 3 px | Humans stop when crosshair is on target |
| Pre-fire ratio | ≤ 15% | More than 15% of kills with first bullet pre-aimed = flag |
| Headshot % | 28-50% depending on rank | Match your rank distribution |
| Spray transfer | ≤ 75% recoil correction | Humans correct ~60-70% |

## Reaction floor

Between the moment a target becomes pixel-visible and the moment your cursor starts moving, inject a delay drawn from a human-looking distribution:

```python
import numpy as np
# human reaction: log-normal centered at 220ms, std 60ms, floor 150ms
reaction_ms = max(150, int(np.random.lognormal(mean=np.log(220), sigma=0.28)))
```

Apply this per-target acquisition, not per correction.

## Flick curves

Linear interpolation is the single biggest cheat tell. Humans accelerate then decelerate.

```python
def bezier_step(dx, dy, t):
    """Quadratic ease-in-out. t in [0, 1]."""
    ease = t * t * (3.0 - 2.0 * t)
    return dx * ease, dy * ease
```

For a correction spanning N ticks, call with `t = i / N` for `i` from 1 to N. The first and last steps are tiny; the middle step is bigger.

Advanced: add mid-correction jitter — small random perturbations perpendicular to the main direction, emulating hand tremor.

## Miss rate

Random misses defeat tracking-ML. Pick 5-15% of corrections and apply a random offset:

```python
if np.random.random() < 0.08:
    dx += np.random.uniform(-14, 14)
    dy += np.random.uniform(-10, 10)
```

Miss rate higher in ranked than in warmup: in ranked, ~10%; in bot TDM, 5% (you want to look better in warmup — matches data).

## Pre-fire ratio

Pre-fire = clicking before the enemy is pixel-visible, hitting them the instant they peek. A cheat with pre-fire on 100% of kills is unmistakable.

Enforce: delay click until N frames after target acquisition. N is drawn from a 3-8 frame distribution (50-130ms @ 60Hz).

For CS2, VACnet specifically weighs pre-fire. Keep below 15% of kills.

## Headshot percentage

Match your rank's distribution. Approximate HS% by rank:

| Rank (Valorant) | HS% median | 95th percentile |
|---|---|---|
| Iron-Bronze | 15% | 28% |
| Silver-Gold | 22% | 35% |
| Plat-Diamond | 28% | 42% |
| Ascendant-Imm | 33% | 48% |
| Radiant | 38% | 52% |

| Rank (CS2) | HS% median | 95th percentile |
|---|---|---|
| Gold Nova | 25% | 38% |
| Master Guardian | 30% | 42% |
| LE-LEM | 36% | 48% |
| Supreme-Global | 42% | 55% |

Set a HS target slightly above your actual legit rank, never above the 95th percentile. GameLab's `vision_aim.py` has `MISS_RATE` + `HEAD_OFFSET` tuning to push HS% up or down.

## Spray transfer

For tracking aimbots on full-auto weapons, don't fully correct recoil. Humans correct ~60-75% of the pattern:

```python
def spray_correct(recoil_dx, recoil_dy, strength=0.68):
    # strength: 0.0 = no correction, 1.0 = perfect
    return -recoil_dx * strength, -recoil_dy * strength
```

Transfer at 100% → VACnet feature trips in ~5 matches of ranked.

## Match pacing

Even perfect behavioral opsec loses if you climb MMR faster than the account tenure justifies.

- New Steam account + CS2 Global in 2 weeks → review queue
- New Riot account + Immortal in 3 weeks → review
- Climb at a plausible rate: 1-2 ranks per month after initial placement

GameLab has `opsec_check` which looks at your `account_age`, `matches_played`, `winrate`, `avg_rank_delta_per_week` and flags red.

## Session variance

Can't have the same elite performance every match. Build variance:
- Occasionally throw a round (die first with no damage done)
- Have below-average games (40% of kills under 15)
- Don't always top-frag

## Streaming / recording

If the rig also streams: viewers screenshot suspicious clips → sent to AC. Public streaming on a cheating account is the single fastest way to get Overwatch'd.

## Reports

Report count triggers review. If your KD is high and you're chatty, people report. Don't be chatty. Don't be visible. Play silent.

## Summary — checklist before a live run

```
[ ] Reaction floor ≥ 150ms set
[ ] Bezier curve enabled
[ ] Miss rate ≥ 0.05
[ ] Max corrections/sec ≤ 11
[ ] Dead zone enabled
[ ] Pre-fire delay ≥ 3 frames
[ ] HS% target within rank's 95th percentile
[ ] Spray transfer ≤ 75%
[ ] Account hygiene check passed (IP, payment, friends)
[ ] MMR pacing on-track
```

If any unchecked — stop and adjust.
