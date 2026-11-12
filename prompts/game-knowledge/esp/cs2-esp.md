---
title: CS2 ESP — hazedumper offsets + external render
tags: [cs2, counter-strike, esp, vac, trust-factor, source2]
source: in-house
updated: 2026-11-12
lifespan_class: months
---

# CS2 ESP

CS2's VAC isn't scary — Trust Factor and VACnet are. The ESP itself is easy; opsec on the behavior is the whole game.

## Data source options

| Source | Lifespan | Difficulty |
|---|---|---|
| External RPM (`ReadProcessMemory`) | months | Easy |
| DMA | invisible | Needs FPGA |
| Internal (manual-mapped, custom code) | months | Medium |
| External vision | invisible | Needs training |

External RPM + Trust-Factor-aware opsec is the standard CS2 approach.

## Offsets

Updated weekly on Tuesdays (CS2 patch day). Public dumps:

- https://github.com/a2x/cs2-dumper — modern dumper, offsets in several formats
- https://github.com/frk1/hazedumper — legacy but still active
- https://github.com/dr-fuzzy/cs2-base — reference base

GameLab imports `offsets.hpp` / `offsets.json` on project start. `gamelab.offsets.refresh` pulls the latest cs2-dumper release.

Key offsets (names stable across patches, values shift):

```
schema_system
  C_BaseEntity::m_pGameSceneNode
  C_BaseEntity::m_iTeamNum
  C_BaseEntity::m_iHealth
  C_BasePlayerPawn::m_vOldOrigin
  CCSPlayerController::m_sSanitizedPlayerName
  CSkeletonInstance::m_modelState -> m_bonesArray
client.dll
  dwEntityList
  dwLocalPlayerController
  dwViewMatrix
```

## World-to-screen

CS2 is Source 2. View matrix is row-major 4×4 at `client.dll + dwViewMatrix`:

```python
def world_to_screen(world_pos, view_matrix, screen_w, screen_h):
    clip_x = view_matrix[0][0]*world_pos[0] + view_matrix[0][1]*world_pos[1] + view_matrix[0][2]*world_pos[2] + view_matrix[0][3]
    clip_y = view_matrix[1][0]*world_pos[0] + view_matrix[1][1]*world_pos[1] + view_matrix[1][2]*world_pos[2] + view_matrix[1][3]
    clip_w = view_matrix[3][0]*world_pos[0] + view_matrix[3][1]*world_pos[1] + view_matrix[3][2]*world_pos[2] + view_matrix[3][3]
    if clip_w <= 0.0:
        return None
    ndc_x = clip_x / clip_w
    ndc_y = clip_y / clip_w
    return ((1.0 + ndc_x) * 0.5 * screen_w, (1.0 - ndc_y) * 0.5 * screen_h)
```

## Entity walk

```python
entity_list = base + dwEntityList
for i in range(1, 64):
    list_entry = read_u64(entity_list + 8 * (i >> 9) + 16)
    if list_entry == 0: continue
    pawn = read_u64(list_entry + 120 * (i & 0x1FF))
    if pawn == 0: continue
    team = read_i32(pawn + C_BaseEntity::m_iTeamNum)
    hp = read_i32(pawn + C_BaseEntity::m_iHealth)
    if team == local_team or hp <= 0: continue
    scene = read_u64(pawn + C_BaseEntity::m_pGameSceneNode)
    bones = read_u64(scene + m_modelState + m_bonesArray)
    head_bone = read_vec3(bones + 6 * 32)   # bone 6 is head, 32-byte stride
    pos2d = world_to_screen(head_bone, view_matrix, 1920, 1080)
    # draw box here
```

## Overlay options

- **External window (second PC)** — zero AC exposure
- **Transparent topmost (same PC)** — VAC + Trust Factor don't catch this directly, but screenshots feed Overwatch
- **Internal (DX11 hook)** — screenshots catch it; VACnet feeds Overwatch

For long lifespan: external window + second monitor or capture card.

## Trust Factor-friendly behavior

See `opsec/behavioral.md`. CS2-specific extras:

- **Spray transfer** — if your "no recoil" is too clean it's a VACnet feature. Transfer at 60-75% strength instead of 100%.
- **Pre-fire ratio** — limit to 15%. Flick to the pre-fire spot and *then* click, with a 50-80ms gap.
- **Wallbang accuracy** — don't wallbang through walls that normal players can't shoot through. The VACnet model has map-geometry priors.
- **Economy sense** — don't win-rate rank-climb at >55% unless your Prime + commend history justifies it.

## Implementation reference

Standalone RPM reader pattern from `cheats/external-vision/world_to_screen.h` and `cheats/dma-stack/pcileech_reader.py` works here too — swap DMA calls for `ReadProcessMemory` if using RPM.

## Current lifespan

- RPM + external window + behavioral opsec: **months**
- DMA + behavioral opsec: **invisible**
- Internal: **months** if custom, **weeks** if public
- External vision only: **invisible**, but no team/HP data

## Per-patch workflow

Every CS2 update:
1. Pull cs2-dumper latest release (`gamelab.offsets.refresh`)
2. Verify view_matrix projection with a known position (pick a smoke grenade — well-rendered world object)
3. Resume
