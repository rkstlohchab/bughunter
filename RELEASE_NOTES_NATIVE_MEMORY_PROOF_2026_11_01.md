# BugHunterClaudex — Verified Native Tool Checks (2026-11-01)

This release continues the previous Mac Control + Memory Automation source archive. It **does not** claim unrestricted system automation, control of Windows Cheat Engine running in CrossOver, or automatic modification of every game. Those capabilities still require integration work and validation on an actual Mac.

## Changes implemented

1. **Live Mac memory reader self-test.** `bridge_self_test` uses the same Mach VM (on macOS) or procfs (on Linux) backend as the memory helper. It allocates a temporary 64-bit number **inside the helper process**, reads its bytes using the backend, writes a replacement through the backend, reads back, restores the original bytes, and checks restoration. It never attaches to, scans, or writes to any outside application and does not disturb an existing process attachment. On failure it reports an error instead of success.
2. **Correct macOS self-process access.** The Mach backend now reuses its own task port for self-inspection rather than requiring `task_for_pid` on itself; it only deallocates ports that it separately obtained for other processes. Its access report identifies external-process access as **unverified** rather than implying that self-read success grants game access. It does not disable SIP or bypass hardened-runtime protections.
3. **Connections in Settings.** The **Settings → Memory helper → Test memory reader** button can start the native bridge for this test, exercise it, then stop the temporary bridge. When another bridge session is already connected, it does not reconnect or interrupt the game session; it uses the same connection for this own-process self-test. The test shows actual evidence, not just `ping` or a successful connection.
4. **App-access probe.** Chat → **Control a Mac app → Test app access** opens the specifically chosen native `.app`, reads visible control labels via macOS Accessibility, and stops. It makes no clicks or keypresses and sends no UI observation to the model. It reports a failure if no readable window appears. This is not a claim that the AI can complete arbitrary GUI tasks.
5. **Repeatable standalone diagnostic.** `python3 scripts/verify_memory_engine_live.py` exercises stdio MCP initialization, tool discovery, and the real self-test and prints a machine-readable result. The existing `scripts/verify_native_tool_control.sh` now runs that diagnostic as part of its checks.
6. **Regression tests.** Tests cover verified read/write/restore, failed writes not being treated as success, isolation from the active game attachment, Mach self-task port lifecycle, and the live stdio roundtrip.

## Test commands

```bash
cd bughunter
python3 scripts/verify_memory_engine_live.py
PYTHONPATH=backend python3 -m pytest -q backend/tests/test_memory_bridge_self_test.py
bash scripts/verify_native_tool_control.sh
npm ci
npm run desktop:dev
```

From the running app, use **Settings → Memory helper → Test memory reader**, then **Chat → Control a Mac app → Test app access** (choose an innocuous app such as TextEdit). Allow Accessibility only through macOS' native permission UI if prompted.

## What the test proves (and does not prove)

- A **passing own-process memory test** proves that the bridge's read and write methods work on a process it owns; it does **not** prove the ability to attach to another process. Whether that is allowed depends on SIP, entitlements, app protections, privileges, and the target process.
- **Test app access** proves that a selected native app exposed readable Accessibility controls. It does **not** prove ability to control every app, every popup, or protected system UI.
- The built-in **Mac memory reader is not Cheat Engine**. A CE executable installed with Wine or CrossOver is not a CE MCP connection. To use CE proper, a compatible separately running Cheat Engine MCP server and supported runtime must be set up and verified. This build does not implement a new CE protocol.
- Burp extension work from earlier releases is retained, but has not been compiled against the real Montoya JAR or tested with your Burp Professional license in this environment.

## Build-environment results

- Focused Python regression suite: 40 passing tests.
- The real bridge self-test passed with the **Linux procfs** backend (this build environment).
- New and existing frontend files passed TypeScript/TSX syntax parsing.
- macOS native build, Accessibility automation, and Mach memory access cannot be executed on this Linux system. They must be tested on a real Mac.
