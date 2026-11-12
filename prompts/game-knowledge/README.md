# game-knowledge — retrieval corpus for GameLab

This directory is imported into the Knowledge FTS5 + vector store at setup time via `scripts/install_gamelab_knowledge.py`. The GameLab AI agent retrieves from it on every substantive question.

Each file carries YAML frontmatter:

```yaml
---
title: Short title
tags: [vanguard, kernel, hwid]
source: in-house
updated: 2026-11-12
lifespan_class: weeks        # hours | days | weeks | months | invisible
---
```

The import script uses `tags` for filtered retrieval (`knowledge.search` with a tag filter narrows to one AC or one stack).

## Layout

```
anticheat/
  vanguard.md             — Riot Vanguard (Valorant, LoL beta)
  vac-trust.md            — VAC + VAC Live + VACnet + Trust Factor (CS2)
  battleye.md             — BattlEye (R6 Siege, DayZ, Tarkov, ARMA, Fortnite)
  eac.md                  — Easy Anti-Cheat (Fortnite, Apex, Rust, Elden Ring MP)
  bgmi-ace.md             — BGMI/PUBGM mobile ACE + emulator detection
  ace-tencent.md          — Tencent ACE / TGPA on PC emulators (GameLoop)
stacks/
  external-vision.md      — Capture card + YOLO + Arduino HID (works everywhere)
  dma-fpga.md             — PCILeech + MemProcFS + KMBox (ring-neg-1 vs AC)
  kernel-mapper.md        — BYOVD + manual map + hooks (ring-0 cheats)
  hypervisor.md           — Type-1 hypervisor cheats (below AC)
  mobile-root.md          — Magisk + Zygisk + GameGuardian (BGMI/COD-M)
esp/
  esp-architecture.md     — Overlay rendering options cross-engine
  bgmi-esp.md             — BGMI ESP via scrcpy or on-device overlay
  valorant-esp.md         — Valorant ESP (world-to-screen + DMA read)
  cs2-esp.md              — CS2 ESP (hazedumper offsets + external overlay)
bypass/
  os-level.md             — OS hardening the cheat hides behind
  kernel-level.md         — Ring-0 strategies vs Vanguard/BE
  hypervisor-level.md     — Below-OS, Type-1 HV approaches
  hwid-spoofing.md        — SMBIOS/disk/MAC/GPU UUID spoofing
  hardware-isolation.md   — Second-PC and DMA architectures
opsec/
  behavioral.md           — Humanization floors (reaction, bezier, miss-rate)
  account-hygiene.md      — IP, payment, email, friends-graph isolation
  match-pacing.md         — MMR climb pacing to stay off review queues
```

## Updating

Edit a file → bump `updated:` in its frontmatter → re-run `python3 scripts/install_gamelab_knowledge.py` to reindex. The script is idempotent per-path, re-chunks on content change, and bumps the FTS5 revision.
