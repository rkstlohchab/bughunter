# Memory scanning playbook

Authored synthesis; reviewed 2026-11-16. No game-specific addresses are asserted: every address is found by measurement on your build.

## The core loop

A value you can see and change on demand is the only reliable anchor. Coins, health, ammo, timers, lives.

1. **First scan** — exact value when the number is on screen; "unknown initial value" when it is a bar, a percentage or hidden.
2. **Change it in-game**, then **refine**: exact new value, or increased / decreased / changed / unchanged.
3. Repeat 3–5 times. Candidates collapse from millions to a handful.
4. **Verify by watching**: read each candidate while the game changes the value. Only one tracks the display.
5. **Write and confirm in-game**. The engine already reads back after a write, but readback only proves the bytes changed. A value that reads back correctly and never appears on screen is a copy, not the source.

## Choosing the type

- **4-byte integer** — most counters, coins, ammo, lives. Try this first.
- **8-byte integer** — large currency, scores past ~2 billion, 64-bit engines.
- **Float / double** — health bars, positions, timers, anything shown with decimals or as a smooth bar.
- **2-byte / 1-byte** — small counters, levels, flags, lives in older engines.
- **Scaled integers** — `75/100` health is often stored as `75`, but may be `7500` (fixed-point) or `0.75` (float). If an exact scan fails on the displayed number, scan "changed/decreased" instead and read what the candidate actually holds.

## When the exact value does not work

- **Unknown initial value** then changed/unchanged refinement. Slower but needs no displayed number.
- **Scan for the change, not the value.** Lose 1 health → "decreased by 1".
- **Encrypted or split values.** Some engines store `value XOR key` or keep a second copy for validation. Symptom: the on-screen number never appears in memory. Find the *code* that reads it instead (see structure discovery), or accept that this target needs static analysis.
- **Value moves every frame.** You found a render-side copy. Scan again while the game is paused.

## From an address to something that survives a restart

Heap addresses change every launch (ASLR plus fresh allocations). A durable mapping is a **pointer path**: module base + static offset → pointer → … → field offset.

1. Find the value address.
2. Run a pointer search from it (the app's `game_pointer_search`, bounded depth and offset window).
3. Restart the game, resolve each candidate path, keep the ones that still land on the value.
4. Repeat on a third launch. A path that survives three restarts is worth saving as a mapping; one that works once is a hypothesis.

Record with the mapping: module name, offsets, value type, game build/version, OS and architecture. Offsets break on every game update.

## Structure discovery

Once you have one field, the rest of the object is usually nearby. Read 128–256 bytes around the address and look for:

- Other plausible counters at 4/8-byte alignment.
- Float triples (x, y, z positions).
- A pointer back to a parent object or a vtable pointer at offset 0 (C++ objects).

Name the offsets relative to the object base, not absolute addresses. That is what makes a mapping portable across launches.

## Freezing

Freezing is a repeated write on a timer. It needs an explicit stop and must end when the session ends. Prefer "restore on stop" for test writes. Watch for a server or a validation routine overwriting your value back — that is a sign the value is checked, not authoritative.

## What will not work, and why

- **Hardened-runtime apps on macOS** refuse `task_for_pid` and lldb without the debugger entitlement. Check with `codesign -dv --verbose=2`. Self-built and ad-hoc signed binaries attach fine.
- **Anti-cheat protected games** detect attach, scanning and writes; an account ban is the usual result, so keep demos on offline targets.
- **Server-authoritative values.** If the server owns the balance, local memory is a display cache: changing it changes nothing real.
- **JIT/managed runtimes** (JVM, .NET, Unity IL2CPP) move objects with garbage collection. Raw addresses go stale quickly; pointer paths from a stable static root are mandatory.

## Engine-aware mapping (Unity IL2CPP and Unreal)

When the engine is known, map structures instead of scanning blind. All of these are read-only and every result is a candidate with evidence, not a fixed offset.

1. `game_engine_detect` identifies the engine from loaded modules and the bundle.
2. **Unity IL2CPP.** `game_metadata_search` lists exact type, field and method names from the build's `global-metadata.dat` (for example search `ammo`). `game_engine_probe probe=il2cpp_class name=<Class>` finds the class record: the base that the rest of memory points at most, plus the measured name-slot offset and namespace. `probe=il2cpp_instances` lists objects whose header is that class; `probe=il2cpp_field` finds the field record and instance-offset candidates.
3. **Unreal.** `probe=unreal_names` derives the name-entry header from the `None`/`ByteProperty` pair and finds the block table; `probe=unreal_objects` derives the object layout from the self-referencing `Class` object and finds object-array entries. Not yet verified on a real Unreal build.
4. Probes run in bounded slices. A receipt with `complete=false` continues with the same `job_id`.
5. `game_object_dissect` an instance, then `game_watch` it while the player acts (fire, reload, take damage), or `game_snapshot` before and after one action. The slot that tracks the action is the field; a matching offset candidate from the field probe confirms it.
6. `game_findings` lists what was measured; `game_finding_promote` turns a confirmed finding into a mapping recipe for revalidation and restart checks.

The watch rate comes from the attach backend, not the requested interval: about 5 samples per second was measured over lldb on macOS. Watch for long enough to see the action several times.

## Evidence to record for every run

OS build, architecture, target name and version, pid, attach path (mach or lldb), address, region permissions, value type, each observed value per refinement step, the write, the readback, the in-game confirmation, and cleanup. A disposable fixture result proves the engine, never a real game.

Reference for the scan methodology: [Cheat Engine tutorial documentation](https://cheatengine.org/help/tutorial.htm).
