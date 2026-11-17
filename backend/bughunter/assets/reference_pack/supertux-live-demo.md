# SuperTux: a verified live demo target

Repository observation; reviewed 2026-11-16. Verified on macOS 27, Apple silicon, against a locally built SuperTux (development version 2975428, SDL3).

## Why this target

SuperTux is open source and built locally, so the binary is ad-hoc signed with no hardened runtime. `task_for_pid` is still refused without the debugger entitlement, but the lldb fallback attaches normally. That makes it the most reliable demo target on a Mac: a real game, a real process, nothing bypassed.

## Verified on this machine

Attaching to a freshly launched `supertux2` and enumerating memory succeeded:

- attach via the persistent lldb session, about 13 seconds on first attach
- 1159 mapped regions, 815 of them writable
- a 4-byte read, a write of a sentinel value, a readback that matched, and a restore of the original bytes

So the full read / write / readback / restore path is proven against a real game, not only against the disposable fixture.

## Build it

```
brew install cmake sdl3 sdl3_image sdl3_ttf physfs glm fmt freetype \
             openal-soft libogg libvorbis libpng zlib curl glew libraqm
git clone --recursive --depth 1 https://github.com/SuperTux/supertux.git
cd supertux && mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_PREFIX_PATH="$(brew --prefix)" \
         -DCMAKE_POLICY_VERSION_MINIMUM=3.5
make -j$(sysctl -n hw.ncpu)
./supertux2
```

Current master needs SDL3, not SDL2. If cmake stops at a missing package, install that one and re-run cmake; the error names it exactly.

## What is actually worth modifying

Read from `src/supertux/player_status.hpp` and `player_status.cpp` of the revision built here, then confirmed against the running process.

| Value | Type | Notes |
|---|---|---|
| `coins` | 4-byte int | **Starts at 100** (`START_COINS`), capped at **9999** (`MAX_COINS`). The best first target: the value exists before you play. |
| `tuxdolls` | 4-byte int | Declared immediately after `coins` in the same object. Find coins, and this is a few bytes away. |
| `bonus` | enum per player | Powerup state (`BONUS_GROWUP` and up). A small int; changing it changes Tux's sprite and abilities. |
| `m_invincible_timer` | Timer (float) | On the `Player` object in `src/object/player.hpp`. The freeze demo: hold it above zero and invincibility never ends. |

Modern SuperTux has no lives counter; do not go looking for one.

Two things that make this a good teaching target:

- **You do not have to play to start.** Coins are 100 on a fresh game, so an exact scan for `100` works immediately. A scan of 4.5 MB of heap on this machine returned **11** 4-byte-aligned candidates holding 100 — small enough to show the whole list, large enough that refinement obviously matters.
- **The 9999 cap is a feature of the demo.** Write 999999 and the counter clamps. That is the honest lesson: the write succeeded, the readback matched, and the game still enforced its own rule. A readback is proof the bytes changed, never proof the game agreed.

## Demo script

1. Launch SuperTux and start a level so the coin counter is visible.
2. In Chat: *"Attach to the running supertux2 process and show me."* The agent measures the target and binds a session; the Game task card appears with the session state.
3. Collect a coin, then ask for an exact scan on the new counter value.
4. Collect another coin and refine. Repeat until a few candidates remain.
5. Ask for a write to the surviving candidate, then look at the HUD. The counter changes on screen.
6. Ask it to save a mapping, quit, relaunch, and revalidate. That shows the difference between an address (gone after restart) and a pointer path (still resolves).

The coin field lives on the player's persistent status object; confirm its member name and type in `src/supertux/player_status.hpp` for the revision you built, rather than trusting any fixed offset.

## One caveat that cost time here

If a previous debugger left the process stopped, a later attach fails with a timeout rather than a clear message. `ps -o stat -p <pid>` showing `T` means stopped. Relaunch the game and attach to the fresh process.

Source: [SuperTux repository](https://github.com/SuperTux/supertux).
