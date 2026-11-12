---
title: ESP architecture — cross-engine overlay techniques
tags: [esp, overlay, rendering, architecture]
source: in-house
updated: 2026-11-12
lifespan_class: varies
---

# ESP (Extra Sensory Perception) overlay architecture

ESP is any visual aid that shows information the game normally hides — enemy positions through walls, health bars, item locations, name tags. The question is always: where does the overlay render, and how does the game see it (or not).

## Overlay rendering options

| Where overlay draws | Pro | Con | AC detection |
|---|---|---|---|
| **Inside the game process (internal)** | Zero latency, perfect overlay | Module whitelist / signature scans | Catches all usermode AC |
| **DirectX/Vulkan swapchain hook (external)** | Low latency, overlay-on-top | Driver-level or injected DLL | Catches on module walk |
| **Transparent window on top** | No injection needed | Any AC that enumerates overlapping windows flags it | Many AC catch this |
| **Second monitor** | Zero AC exposure on gaming PC | Eye movement during play | None |
| **ESP-only second PC monitor from capture card** | Zero AC exposure | Eye movement; latency-matched to vision | None |
| **AR glasses / Stream Deck display** | Zero AC exposure, minimal eye movement | Hardware cost | None |

For AC survival, the second-PC path is default. For pure speed on untended ACs (CS2, old EAC on Linux), internal is fine.

## Data path

ESP requires per-frame knowledge of:

1. **View matrix** (view-projection matrix) — to project world coordinates to screen.
2. **Entity list** — array of players/pickups/pawns.
3. **Per-entity position, health, team, name** — for each visible entity.
4. **Local player position + team** — to know "friend or foe."

This data is read either:
- From game process memory (internal cheat, external RPM, DMA FPGA)
- From CV inference (YOLO on frames) — shape-matches but no team/health numerics
- Hybrid (DMA for team/health, vision for fallback when memory structures move)

## World-to-screen math

Given a 4×4 view-projection matrix `M` and a world position `(x,y,z)`:

```
clip.x = M[0]*x + M[1]*y + M[2]*z + M[3]
clip.y = M[4]*x + M[5]*y + M[6]*z + M[7]
clip.z = M[8]*x + M[9]*y + M[10]*z + M[11]
clip.w = M[12]*x + M[13]*y + M[14]*z + M[15]

if clip.w <= 0: entity is behind camera, cull
ndc.x = clip.x / clip.w                  # [-1, 1]
ndc.y = clip.y / clip.w                  # [-1, 1]
screen.x = (1 + ndc.x) * 0.5 * viewport.w
screen.y = (1 - ndc.y) * 0.5 * viewport.h
```

Row-major vs column-major matrices flip the index set — most Unreal titles are row-major, most Source titles column-major. Verify with a known position.

See `cheats/external-vision/esp_overlay.py` for working code.

## Rendering options (second-PC overlay)

Second-PC ESP renders boxes + names + health on a PC window that shows the capture-card video. Options:

- **PyQt / PySide** — Python window with QPainter boxes on top of a QLabel video. Quick, 60 fps.
- **ImGui over SDL/GLFW** — fast, native feel, GPU-accelerated.
- **OpenCV `imshow`** — fastest to write, no decorations.
- **Second monitor on gaming PC through remote desktop** — pulled from the second PC's screen capture, but adds latency.

For GameLab, the second-PC overlay is PyQt + OpenCV (see `cheats/external-vision/esp_overlay.py`).

## Internal ESP (for games with weak AC)

When the AC is weak (CS2 Steam Deck, EAC Linux, older BE titles), internal ESP is the highest-quality option:

- Inject a DLL (MinHook + DX11/12 swapchain hook)
- Hook `IDXGISwapChain::Present`
- After the game draws its frame, draw your ImGui / DirectX overlay on the same backbuffer
- Read entity list + matrix from in-process memory

No capture card, no external PC, no model training.

Caveats:
- Game screenshots include your overlay (AC that samples screenshots sees it instantly)
- Module whitelist catches your DLL
- Any kernel-mode AC catches the injection

Appropriate for lab / offline / weak-AC targets only.

## ESP data for games without obvious entity arrays

Some modern Unreal titles obfuscate entity lists. Fallbacks:

- **GWorld → ULevel → APlayerControllerArray → APawn** — the canonical chain. Offsets re-derive per build.
- **AActor loop by name** — iterate every AActor; filter by `IsA` on `ACharacter` class — slow but robust.
- **Render-queue inspection** — hook the mesh draw calls; every "player" mesh draw gives you a world position of the actor doing the draw. Slow, but offset-independent.
- **Pure CV** — if memory layouts churn, skip memory entirely. Train YOLO on enemy sprites; project detections back to screen. Used for mobile BGMI since on-device memory reads are banlines.

## Visibility checks (inside walls vs line-of-sight)

Many ESP implementations color box differently when the enemy is visible vs through walls. Tells:

- Game's own visibility flag (if exposed) — some engines have `AActor::bRenderInMainPass` or similar.
- Raycast from camera to entity using the game's collision — too slow if more than ~16 entities.
- Z-buffer sampling at the screen coordinate — compare rendered depth to entity's expected depth.
- Just color everything one color — simpler; still useful.

## Latency targets

| Budget | Pipeline must fit in |
|---|---|
| 60Hz gameplay, feel "instant" | <33ms end-to-end |
| Competitive, keeps up with flicks | <50ms |
| Still playable | <100ms |

See `stacks/external-vision.md` for the latency table of a typical external build.

## Next steps

- Per-game specifics: `esp/bgmi-esp.md`, `esp/valorant-esp.md`, `esp/cs2-esp.md`
- Pick your data path first (vision / RPM / DMA / internal)
- Render on the PC you've chosen (second PC for external, game process for internal)
