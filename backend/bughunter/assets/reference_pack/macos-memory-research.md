# macOS process-memory research workflow

Authored synthesis and repository observations; reviewed 2026-11-16. No game-specific offsets are asserted.

## Can the app attach at all?

The native macOS engine reads and writes through `task_for_pid` and falls back to one persistent `/usr/bin/lldb -p <pid>` session. Both are refused when the target runs with the hardened runtime and lacks `com.apple.security.get-task-allow`. [Apple hardened runtime](https://developer.apple.com/documentation/security/hardened-runtime), [get-task-allow entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.get-task-allow).

Check before you spend time on a target:

- `codesign -dv --verbose=2 /path/App.app 2>&1 | grep flags`: a `runtime` flag means hardened runtime.
- `codesign -d --entitlements - /path/App.app`: look for `get-task-allow`.
- Ad-hoc or unsigned builds you compiled yourself normally have neither restriction. They are the most reliable practice targets.
- Mac App Store apps and iOS apps running on Apple silicon are protected. Do not plan a task around them.

If attach fails, report the exact `kern_return` or lldb error. Do not suggest disabling SIP as a routine step.

## Finding a value (the scan loop)

1. Pick a value you can see and change on demand: coins, money, ammo, health.
2. First scan: an exact value when it's shown, otherwise "unknown initial value".
3. Change the value in-game, then rescan: exact new value, or increased/decreased/changed/unchanged.
4. Repeat until a handful of candidates remain. Three to five changes usually suffice.
5. Read each candidate while the game changes the value. A real match tracks the on-screen number.
6. Write a distinct test value and read it back (the engine does this automatically). Then confirm the game displays or uses it. A readback that matches but never shows up in-game is a shadow copy, not the source of truth.

Type matters. Try 4-byte integers first, then 8-byte integers (large currencies), then float or double (health bars, timers). Small counters can be 2-byte. Values shown as `x/100` are often floats scaled differently from the display.

## Making it survive a restart

Heap addresses change every launch (ASLR plus fresh allocations). A durable mapping is a pointer path: module base + static offset → pointer → … → field offset. Record the module name, offsets, value type, and the game build or bundle version it was found on. Re-verify after relaunch. A path that works once is a hypothesis, not a mapping.

## Writes and freezes

- Write only after a successful read of the same address in the same session.
- Freezing means rewriting a value on a timer. It needs a stop control and must end when the session ends. The engine retires freezes on detach.
- Prefer "restore on stop" for test writes so the game returns to its own state.
- An `outcome_unknown` write is not retried automatically. Investigate before sending another.

## Evidence to record

OS build, CPU architecture, target bundle ID and version, PID, attach path (mach or lldb), address and region permissions, value type, observed values per step, readback result, and cleanup outcome. A disposable fixture result proves only the engine, never a real game.
