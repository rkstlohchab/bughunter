---
title: Match pacing — MMR climb without triggering review
tags: [opsec, mmr, ranked, pacing]
source: in-house
updated: 2026-11-12
lifespan_class: always
---

# Match pacing

Climbing faster than account tenure justifies is the biggest flag after obvious aim-tells. Pace the climb.

## The pacing rule

Climb at a rate that a top-5% legit player at your account's hours could plausibly hit. Rough caps:

| Game | Climb cap (ranks / month after placement) |
|---|---|
| Valorant | 1-2 (Iron → Silver in a month, Silver → Gold in a month, …) |
| CS2 | 2-4 (Trust Factor gates speed, especially fast climbs) |
| BGMI | 1-2 tiers per season |
| R6 Siege | 1 rank / month |
| Apex | ~200 RP / day baseline, surge to 400 on good days |

Faster = review queue. Especially fast = direct ban-wave inclusion.

## Winrate caps

Over a 50-game rolling window:
- < 55% winrate: no flag
- 55-62%: okay for a "good player," expected at top ranks
- 63-70%: review-queue territory
- 70%+: ban-wave material

Deliberately throw a few rounds per week to stay in the 55-62% window. Not throwing games — just playing worse: aim low, don't fire first, miss one easy shot per round.

## Kill:death and ADR

Stats per game:
- CS2: ADR 70-100 is high-trust. ADR 150+ consistently = review.
- Valorant: ACS 250-320 at mid ranks. 400+ consistently = flag.
- Apex: 2000+ damage per game consistently at Platinum = flag.

Calibrate your HS% + miss rate to hit a plausible ADR/ACS for your current rank.

## Session length

Marathon sessions are a flag signal if winrate is high. 8h session with 75% winrate = reviewed.

- Keep sessions to 3-4h max
- Take a 1-2 day break every 2-3 weeks
- Stop a session after 2 straight losses (don't tilt-win the cheat into a ban wave)

## Account age cliff

Riot and Steam have an age multiplier on their review threshold. An account < 1 month old with Immortal-rank performance = instantly reviewed. Older accounts are allowed more leniency.

Strategies:
- Buy aged accounts (not recommended — many are already shadow-flagged)
- Build the account legitimately for 1-2 months before enabling the cheat
- Enable cheat only for 1 session per week on a new account, then pause

## Tournament / event risk

Playing in a tournament or event that includes server-side demo review is a review guarantee. Don't.

## "Hitch and smurf" pattern

The worst pacing anti-pattern:
- Make account, grind to rank-ceiling fast
- Get banned
- Repeat

AC vendors fingerprint this pattern by payment/email/IP reuse. Burn fully between accounts — no reused edges.

## Visible behavior to adjust per rank

- Low ranks: deliberately miss some easy kills. New low-rank players don't 1-tap.
- Mid ranks: play riskier (push angles without info). Legit mid-rank players are overconfident.
- High ranks: play slower, hold angles with better setup. Legit high-rank players value information.

GameLab's `opsec_pacing_check` RPC takes your current stats and flags whether the pacing is in bounds.

## Reports

Over-reports from humans trigger review. You can't control reports entirely, but:
- Don't talk in voice
- Don't dominate every round visibly (break up good rounds with "off" rounds)
- Don't play with others who are known for calling cheaters
- Don't post montages from this account

## Recovery after a flag

If a Trust Factor hit happens (CS2) or a warning (Valorant):
- Stop the cheat
- Play legit for 1-2 weeks
- Let the account marinate; some flags decay with legitimate play
- When you resume, start from the lowest-risk game mode (unranked)

## Multi-account pacing

If running multiple accounts:
- Space their peak play times (don't both play ranked 8pm-11pm every night)
- Different climb rates per account
- Different behavioral tuning per account (slightly different reaction floor, miss rate) so a reviewer looking at both won't see identical patterns
