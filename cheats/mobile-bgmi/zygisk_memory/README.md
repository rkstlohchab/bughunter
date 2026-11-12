# zygisk_memory — on-device memory reader for BGMI / COD-M / Free Fire

A Zygisk native module that loads into the target game process, reads
its memory, and ships entity snapshots to a PC over an abstract UNIX
socket (`localabstract:gamelab_mem`). Pair with
`adb forward tcp:65510 localabstract:gamelab_mem` on the PC; the
GameLab `bgmi` reader connects to localhost:65510 and drives the
universal ESP overlay.

## Phone requirements

- Magisk ≥ 26.1 with Zygisk enabled
- Shamiko (or Zygisk-Assistant) hiding Magisk from the game
- Root detection hidden (see `prompts/game-knowledge/stacks/mobile-root.md`)

## Build

Install the Android NDK (`r26+`), then:

```bash
cd cheats/mobile-bgmi/zygisk_memory
ndk-build -j
# Output: libs/arm64-v8a/libgamelab_mem.so + libs/armeabi-v7a/libgamelab_mem.so
```

Package into a Magisk module ZIP:

```bash
./scripts/make_module_zip.sh
# → gamelab_memory.zip
```

Flash via Magisk Manager → Modules → Install from storage.

Enable Zygisk DenyList entries for your target games:
- `com.pubg.imobile` (BGMI)
- `com.tencent.ig` (PUBG Mobile)
- `com.activision.callofduty.shooter` (COD Mobile)
- `com.dts.freefireth` (Free Fire)

## Connect the PC

```bash
adb forward tcp:65510 localabstract:gamelab_mem
```

Then run the BGMI reader:

```bash
# via GameLab adapter:
gamelab.start_recipe adapter=memory_esp config={
  game: "bgmi",
  reader_backend: "android",
  mobile_endpoint: "127.0.0.1:65510",
  offsets_path: "cheats/game_data/bgmi.offsets.json"
}
```

The reader streams frames → universal overlay on PC. Chams, snaplines,
bones, HP, distance, team, weapon — all from phone memory.

## Protocol — the Zygisk side speaks

Newline-terminated JSON over the socket. Requests PC → phone:

| cmd | fields | reply |
|---|---|---|
| `module` | `name` | `{base: <int>}` |
| `read` | `addr`, `size` | `size (u32 little)` + `size` bytes |
| `view` | — | `{matrix: [16 floats]}` |
| `local` | — | `{ok, id, team, hp, head, feet, center, bones}` |
| `players` | — | `{players: [Entity...]}` |
| `loot` | — | `{items: [...]}` |

The server-side filler of these fields is `memory_scanner.cpp`, which
walks UE4 GWorld → PersistentLevel → Actors — same semantics as the PC
Unreal walker, just inside the game process.

## Files

- `jni/Android.mk`, `jni/Application.mk` — NDK build config
- `jni/main.cpp` — Zygisk hook entry; starts the socket server on fork
- `jni/socket_server.cpp` — accept + dispatch loop
- `jni/memory_scanner.cpp` — entity list walker (UE4 layout for BGMI)
- `jni/zygisk.h` — Zygisk API header
- `jni/common.h`
- `scripts/make_module_zip.sh` — package Magisk ZIP

## Per-game offsets

Offsets drift per BGMI update (every 2-3 weeks). The PC-side reader loads
them from `cheats/game_data/bgmi.offsets.json`. The Zygisk side reads the
same file via a `pull` request at connection time — so updating offsets
is an offsets.json edit on PC, no re-flash of the module.

## Lifespan

Zygisk hidden + Shamiko + a stock build fingerprint typically lasts 1-4
weeks between anogs bypass updates. The failure mode is anogs detecting
a new signature in the module; swap to a renamed `.so`, re-flash.

## Security

The socket is `localabstract:gamelab_mem` — only accessible via `adb
forward`, which requires USB debugging ON. Not exposed to other apps on
the phone.
