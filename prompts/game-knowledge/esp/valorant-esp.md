---
title: Valorant ESP — DMA or external vision only
tags: [valorant, esp, vanguard, dma, unreal]
source: in-house
updated: 2026-11-12
lifespan_class: invisible-dma, invisible-vision
---

# Valorant ESP

Vanguard rules out everything internal and most kernel-mapper work (see `anticheat/vanguard.md`). Realistic options are two:

## Option 1 — External DMA (highest quality)

- FPGA card in gaming PC, Python reader on second PC. See `stacks/dma-fpga.md`.
- Walk `GWorld → PersistentLevel → Actors → APawn[]`
- Read local player's `PlayerCameraManager` for view matrix and camera position
- For each enemy pawn: `RootComponent->RelativeLocation` for world position
- `HealthComponent->HP` for health; `TeamComponent->TeamId` for team-filter
- Project world → screen using the view-projection matrix
- Draw boxes in a PyQt window on the second PC, showing the live capture video underneath

Per-build offsets live in `cheats/dma-stack/offsets/valorant.json`. GameLab has `game_offsets.refresh` to re-derive after each patch — the AOB signatures are stable even when offsets shift.

Example pointer chain (symbolic; derive per build):

```
UWorld*       = *(uint64_t*)(valorant_base + 0x08B23C78)
ULevel*       = *(UWorld* + 0x30)
TArray<AActor*> = *(ULevel* + 0x98)   # Actors array
count         = *(int32_t*)(ULevel* + 0xA0)
for i in range(count):
    AActor* actor = Actors[i]
    FName class_name = actor->GetClass()->FName
    if class_name == "APawn" or "ACharacter" subclass:
        float3 pos = actor->RootComponent->RelativeLocation
        float hp  = actor->HealthComponent->HP        # hollow struct — walk
```

## Option 2 — External vision + YOLO

- Capture card on second PC, YOLOv8 trained on Valorant agent silhouettes
- Train set: 2000-3000 frames across all 20+ agents, on all maps, at varied distances
- Head offset at 20-22% from top of bbox (Valorant hitboxes are tight — head is small)
- Draw boxes on second-PC display

Pro: no offset re-derivation after Valorant patches. Con: no team/health data (only shape); no box color-by-visibility.

Hybrid: DMA reads team/health; vision confirms + provides fallback when offsets break on patch day.

## Agent-specific tuning

Valorant hitboxes differ per agent. YOLO training that doesn't balance across agents gets worse on under-represented ones (e.g. tall agents like Viper read differently than short ones like Raze).

## FOV + sens

Valorant native sens stops at 0-10 with multipliers. For aimbot delta scaling:

```
pixels_per_degree = viewport_w / (2 * atan(game_fov_x * π / 360))
delta_pixels = angular_delta_deg * pixels_per_degree
delta_input_counts = delta_pixels / dpi_ratio / sens_multiplier
```

GameLab's `vision_aim.py` has a per-game `SENS_SCALE` config. Valorant typical: 2.4-2.8 for 1080p, 800 DPI, 0.35 in-game sens.

## Overlay rendering

Second-PC only. Internal / transparent-window / DX hook — all detected. The overlay lives on your capture-card preview window. Keep it on the second monitor; only glance at it, don't watch it.

Alternative: project it onto AR glasses (XREAL, Rokid) that take HDMI directly — the capture card output feeds HDMI through to the glasses. Zero eye movement.

## Behavioral opsec (mandatory)

Vanguard's ML is the real wall even on a perfect DMA build.

- Reaction floor: ≥ 180ms
- Flick curve: quadratic bezier, not linear
- Miss rate: ≥ 0.08
- HS%: cap at 50% (your rank peers average 28-38%)
- Pre-fire ratio: cap at 15%
- Spray-transfer: don't auto-correct recoil below 30% strength

See `opsec/behavioral.md`.

## Current lifespan

- DMA + KMBox + humanization: invisible to Vanguard; account lifespan = your behavioral opsec
- External vision + Arduino HID + humanization: same
- Internal: hours
- Kernel-mapper: days-weeks

## Per-patch workflow

Every Valorant patch (every 2 weeks):

1. Pull new game executable via DMA dump (`memprocfs/M/{pid}/dll/valorant-win64-shipping.exe`)
2. Run signature scan: `python3 cheats/dma-stack/offset_finder.py --image val.exe --sigs valorant.sig.json`
3. New offsets land in `valorant.json`
4. Hot-reload: `gamelab.offsets.refresh` → pipeline resumes
