# Practice targets on macOS (offline, open source)

Authored synthesis; reviewed 2026-11-16. Field names come from each project's public source. Verify them against the exact revision you build, because names and types change between releases.

## Why open source and self-built

A locally built binary is ad-hoc signed or unsigned, so `task_for_pid` or the lldb fallback can attach. You can also read the source to confirm the type and structure behind a value you found by scanning. That turns a scan result into verified evidence instead of a guess. Use offline single-player modes only.

## Recommended ladder

**1. SuperTux, a 2D platformer (beginner).** [Source](https://github.com/SuperTux/supertux). The coin counter is always on screen and changes on every pickup, which makes it ideal for an exact-value scan of a 4-byte integer. In the source, the player's persistent status (coins, bonus state) lives in the `PlayerStatus` class. Confirm the `coins` member's type in `src/supertux/player_status.hpp` for your revision. Good first exercise: find coins, write a new value, confirm the HUD shows it, then relaunch and rebuild the pointer path.

**2. OpenTTD, a transport tycoon simulation (intermediate).** [Source](https://github.com/OpenTTD/OpenTTD). Company money is a 64-bit integer (`Money`, an overflow-safe int64 wrapper). The HUD may show it in a different currency unit than the stored base value, so scan for "changed/increased/decreased" rather than the displayed number. Good exercise: 8-byte scans, display-versus-storage scaling, and values that change continuously.

**3. Cave Story through NXEngine-evo, an action platformer (intermediate).** [Source](https://github.com/nxengine/nxengine-evo). Health and weapon ammo are small integers on a player object. Check the player structure in the source for exact widths; 2-byte values are common in ports of older engines. Good exercise: small-width types and values that reset on room transitions.

**4. Mindustry, a factory/tower-defence game in Java (advanced).** [Source](https://github.com/Anuken/Mindustry). The JVM garbage collector moves objects, so raw heap addresses go stale quickly. Good for learning why pointer paths from a stable root matter. Expect more failed relaunch verifications.

## Built-in target first

Before any real game, run the app's **disposable test target** from Chat ("Start disposable test target"). It exercises attach, read, write, readback and cleanup against a process the app owns. A pass proves the engine path on this Mac; it proves nothing about a specific game.

## Targets that will not work

Anti-cheat games detect attach and writes; an account ban is the usual result. Mac App Store and iOS-on-Mac apps refuse attach anyway (hardened runtime). Mobile games such as Subway Surfers are not native macOS targets — those go through the APK workflow instead.
