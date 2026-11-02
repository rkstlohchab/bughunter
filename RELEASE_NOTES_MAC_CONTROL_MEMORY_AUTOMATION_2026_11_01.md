# BugHunterClaudex — Mac Control + Verified Memory Changes (2026-11-01)

## Actual additions in this source release

1. **macOS app automation** — menu observation and named menu selection, named list-row selection, page scrolling, and the existing button/tab/field/keyboard actions. The Python planner verifies UI labels against the current Accessibility observation; native Rust independently validates the requested action. The selected application's focus is rechecked before every action.
2. **Responsive Stop** — the Tauri desktop session now has a shared cancellation token. It does not hold the session lock while an AppleScript runs; Stop can cancel a pending AppleScript child. Overlapping actions are rejected.
3. **Permission guidance** — check macOS Accessibility and Screen Recording permission directly from the Chat control panel. On explicit request, open Apple's Accessibility settings page and request Screen Recording through Apple's native prompt. These controls never grant access themselves.
4. **Memory bridge heartbeat** — reports the selected backend (built-in Mac memory vs. external Cheat Engine MCP), the result of a live protocol round trip and advertised tool capabilities. On the native backend, it reads the current attached PID if the memory server reports one. Unknown process status is **not** presented as success.
5. **Game-memory value modification workflow** — the built-in Mac memory MCP adds `change_unique_scan_result`. It acts only on exactly one complete, typed numeric scan result, compares the current process value against the observed and expected values before writing, then reads the new value back. It rejects stale scans, truncated scans and ambiguous candidates. The native approval policy explicitly requires human approval for this operation. Previous general memory tools remain available.
6. **Guided UI** — "Find running games", "Check selected game", and "Check real connection" are available in the memory helper. Chat's existing memory handoff remains available.

## What this is NOT

- **Not unrestricted control of the entire Mac.** The macOS agent operates one explicitly selected native app at a time, within a timed session, without shell access or automatic privilege escalation. Credentials, system preferences, and high-impact UI commands remain protected.
- **Not guaranteed arbitrary-game modification.** Some games use protected processes, kernel drivers, anti-cheat, encrypted state, or server-side values. macOS memory access needs system permissions/entitlements and may fail even for local apps. This code is intended for apps you own or authorized offline testing.
- **Not verified direct Cheat Engine control on Mac.** On macOS the built-in memory MCP is a separate implementation. The Cheat Engine backend depends on a compatible externally running Cheat Engine MCP server (typically via Windows/Wine/CrossOver). Its presence is not inferred from the Mac native memory reader.
- **No live Mac build or game test** was possible in the Linux build environment. The macOS AppleScript, native permission dialogs, Tauri/Rust compilation, and Wine/CE operation require testing on your actual Mac.

## Mac-side verification checklist

1. Make a backup of your local BugHunterClaudex data before switching versions.
2. Unzip this source archive and run `python3 scripts/setup.py`, then `npm run desktop:dev`.
3. In Chat, open **Control a Mac app**, choose a benign test app and check Mac permissions. Grant access in Apple's Privacy & Security settings when prompted.
4. Ask Chat to inspect the UI, select an observed non-destructive menu item or list row, then press Stop during an interaction. Check that it cancels.
5. In the memory helper, click **Start memory helper**, then **Check real connection**. Verify that the label distinguishes the built-in Mac reader from Cheat Engine. Use **Find running games** to inspect the process list without attaching.
6. On a test program that you own, attach with approval, scan an observed numeric value, change it in the test program, narrow it to one result, request `change_unique_scan_result`, approve, then check the actual program and tool readback. If a scan is ambiguous, the tool must refuse to write.
7. Run `bash scripts/verify_native_tool_control.sh` for local source-level checks.

## Tests performed in the build environment

- 30 targeted Python backend regression tests passed, covering verified memory changes, planner bounds, Burp regressions and previous memory helpers.
- TypeScript/TSX syntax transpilation completed for the changed frontend files.
- Python source compilation and ZIP integrity checks passed.
- A complete Tauri/macOS build and live CE integration could not be verified here.
