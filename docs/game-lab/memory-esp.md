# Universal memory-ESP pipeline

GameLab's memory-ESP pack: one framework + one overlay renderer + 13
game walkers. Boxes, head dots, snaplines, 2D skeletons, HP bars,
names, distance, weapon labels, chams, 2D radar — every primitive, same
code path, every game that has an engine walker.

## Architecture

```
┌──────────────────┐   ┌─────────────────────┐   ┌──────────────────┐
│ MemoryReader     │──▶│ EngineWalker        │──▶│ Universal Frame  │
│  (DMA / RPM /    │   │  Source2 / Unreal / │   │  (one schema,    │
│   Android Zygisk)│   │  Unity / Apex /     │   │   every game)    │
└──────────────────┘   │  AnvilNext)         │   └────────┬─────────┘
                       └─────────────────────┘            │
                                                          ▼
                                                 ┌──────────────────┐
                                                 │ FrameEmitter     │
                                                 │  → shared file   │
                                                 │  → UDP 65501     │
                                                 │  → in-proc cb    │
                                                 └────────┬─────────┘
                                                          │
         ┌────────────────────────────────────────────────┼──────┐
         ▼                                                ▼      ▼
┌────────────────────┐                        ┌────────────────┐ ┌──────────────┐
│ esp_overlay.py     │                        │ radar_window.py│ │ combat_loop  │
│  box/head/snapline │                        │  2D top-down   │ │ pick target  │
│  skeleton/HP/name  │                        │  rotatable     │ │ + humanize   │
│  distance/chams    │                        │                │ │ + HID out    │
└────────────────────┘                        └────────────────┘ └──────────────┘
```

Every primitive is one of three overlay processes. Each consumes the
same `Frame` JSON. Start what you need, stop what you don't.

## Supported games

| Game | Engine | Walker | Reader backend | Notes |
|---|---|---|---|---|
| CS2 | Source 2 | `Source2Walker` | DMA / RPM | Offsets auto-pull from `a2x/cs2-dumper` |
| Valorant | UE4 | `UnrealWalker` | DMA only | Vanguard; behavioral opsec is the wall |
| Apex Legends | Source (Respawn) | `ApexWalker` | DMA / RPM | Supports loot ESP |
| Rust | Unity IL2CPP | `UnityIl2CppWalker` | DMA / RPM | Loot ESP supported |
| Fortnite | UE5 | `UnrealWalker` | DMA / RPM | — |
| R6 Siege | AnvilNext | `R6SiegeWalker` | DMA / RPM | BattlEye |
| Tarkov | Unity | `UnityIl2CppWalker` | DMA only | Aggressive BE; DMA + opsec |
| COD MW/WZ | IW engine | `CodMwWalker` | DMA only | Ricochet |
| PUBG PC | UE4 | `UnrealWalker` | DMA / RPM | — |
| **BGMI** | **UE4 (mobile)** | **`AndroidNativeWalker`** | **Zygisk bridge** | **Memory ESP, no model training** |
| **COD-Mobile** | UE4 (mobile) | `AndroidNativeWalker` | Zygisk bridge | — |
| **Free Fire** | Unity (mobile) | `AndroidNativeWalker` | Zygisk bridge | — |
| FiveM / DayZ | RAGE / Enfusion | planned | — | Walker TODO |

## One-click: start the full kit

```
gamelab.start_full_kit project_id=<id> features={
  memory_esp: true, radar: true, aim: false, trigger: false, recoil: false,
  draw_box: true, draw_head_dot: true, draw_snapline: true,
  draw_skeleton: true, draw_name: true, draw_hp_bar: true,
  draw_distance: true, draw_weapon: true, draw_loot: false,
  chams_mode: "team"
} hid={ backend: "arduino", arduino_port: "/dev/ttyACM0" }
```

Spawns three child processes: memory_esp reader, radar window, combat
loop (if `aim:true`). Everything consumes the same frame stream.

In the GameLab UI panel, tick the feature boxes and click **Start full
kit**.

## PC flow (CS2 / Valorant / Apex etc.)

1. DMA hardware installed on the gaming PC (see `setup-dma.md`)
2. `pip install memprocfs numpy opencv-python pyserial` on the reader PC
3. `gamelab.offsets.refresh game=cs2 source=community` (auto for CS2)
4. In the GameLab UI → the project → **Start full kit** with the
   features you want.
5. Overlay windows appear on the reader PC's screen. Put them on your
   second monitor or on AR glasses fed from the reader PC's HDMI.

## Mobile flow (BGMI / COD-M / Free Fire) — memory ESP, no training

1. Root your phone with Magisk (see `setup-bgmi.md`)
2. Enable Zygisk + Shamiko, add game package to DenyList
3. Build the Zygisk module:
   ```
   cd cheats/mobile-bgmi/zygisk_memory && ndk-build -j
   bash scripts/make_module_zip.sh
   ```
   Flash `gamelab_memory.zip` via Magisk Manager. Reboot phone.
4. Plug phone to PC, `adb devices` shows it.
5. Edit `cheats/game_data/bgmi.offsets.template.json` with the current
   BGMI offsets (community-maintained) → save as
   `<data_dir>/gamelab_offsets/bgmi.json`.
6. In GameLab UI → create a `bgmi` project → **Start full kit**. The
   `mobile_memory_esp` adapter:
   - runs `adb forward tcp:65510 localabstract:gamelab_mem`
   - pushes the offsets to the Zygisk module via socket
   - spawns the memory_esp reader with `reader_backend=android`
   - spawns the universal overlay
7. The overlay opens on your PC. Boxes/snaplines/skeleton/HP/distance/
   team from real BGMI memory. No YOLO. No training.

## Overlay stand-alone

If you just want to see what a reader is producing:

```bash
python3 cheats/overlay/esp_overlay.py --input shared_file \
    --shared-file /tmp/gamelab_frame_cs2.json --chams team
```

Or the radar:

```bash
python3 cheats/overlay/radar_window.py \
    --shared-file /tmp/gamelab_frame_cs2.json --range 300
```

## Per-patch offset refresh

When the game patches:

```
gamelab.offsets.refresh game=cs2 source=community   # for CS2
# Other games:
gamelab.start_recipe adapter=dma_memread config={
  mode: "offset_scan", game: "valorant",
  process_name: "VALORANT-Win64-Shipping.exe"
}
```

Then:

```
gamelab.start_recipe adapter=memory_esp config={
  mode: "restart_reader", reader_pid: <pid>
}
```

The overlay keeps running; only the reader restarts with fresh offsets.

## Honest lifespan

- Offsets drift every game patch (weekly CS2, biweekly Valorant,
  monthly for most others, 2-3 weeks for BGMI).
- Zygisk module hidden via Shamiko lasts 1-4 weeks between anogs
  bypass updates — then rename the `.so`, re-flash.
- Vanguard behavioral ML always gates: tune humanization per
  `prompts/game-knowledge/opsec/behavioral.md`.

## What's still per-your-build-engineering

- BYOVD vulnerable driver choice (kernel-level cheats; not needed for
  DMA or Zygisk paths)
- Per-game AOB signature sets for games without community dumpers
  (template skeletons shipped in `cheats/dma-stack/signatures/`)
- Current-season offsets: community maintains these; swap as patches
  land.

The infrastructure is here. The infrastructure is the sustainable
engineering. Offsets are the always-moving part — one RPC refreshes
them, no code changes needed.
