# BugHunterClaudex — Live Memory Tracking & Offline ESP Lab

## What's new

The **native macOS memory MCP helper** (`src-tauri/resources/mac_memory_mcp.py`) now exposes four additional **read-only** tools:

- `watch_value`: samples a typed numeric value (such as health or money) between 1 and 40 times, re-evaluating the supplied pointer chain at every sample. Maximum 15-second observation, with real process ID and observations returned. Does **not** assert that an address is stable after restart.
- `list_loaded_modules` / `verify_module_pointer_chain`: determine observed module load addresses from Linux `/proc/<pid>/maps` or macOS `vmmap`, then rebase a **supplied and previously verified** module-relative pointer recipe (module name + RVA + offsets). The macOS `vmmap` parser remains unverified on Apple Silicon and returns errors rather than guessed addresses. One check is not proof of restart stability.
- `read_entity_layout`: reads up to 32 typed records from a user-supplied structure layout (numeric `x`, `y`, `z`, `team`, `health`, `id`, `visible`). The tool does **not discover** unknown entity layouts or guess offsets. It does not write memory.

The existing `scan_value`, `next_scan`, `find_pointer_references`, `verify_pointer_chain` and approval-gated `change_unique_scan_result` / `write_memory` remain available. The Mac AI tool prompt and Chat routing now recognize requests to locate, monitor, or change health/money values and inspect entity coordinates. Writes and attachments remain approval-gated by Tauri's policy.

## Run the real offline experiment on your Mac

After using the existing `bash setup-mac.sh --core-only` installer:

```bash
cd ~/Downloads/bughunter
python3 scripts/verify_game_memory_restarts.py
```

This launches **two different child game processes** with changing memory addresses. The program supplies its own struct layout and pointer slot. The verifier attaches to each process, resolves the pointer chain, reads health, samples health, and inspects the four simulated entities. No changes are made by default.

To verify an actual modification **only on these disposable child processes**, then restore the value:

```bash
python3 scripts/verify_game_memory_restarts.py --write-lab
```

To save the observed evidence:

```bash
python3 scripts/verify_game_memory_restarts.py --output reports/offline_restart_evidence.json
```

### Live 2D radar, based on real external-process reads

```bash
python3 scripts/run_esp_lab_demo.py
```

The test program and memory bridge start locally, and the radar opens at <http://127.0.0.1:8787>. It polls real entity positions from the child game process using `read_entity_layout`. If the memory attach is denied, the page explicitly displays the error rather than showing fabricated positions. The HTTP server listens on **loopback only**. Stop with `Ctrl+C`.

This is a *browser-based ESP-style radar for the included offline game*, not an injected overlay on a third-party game's window. It does not bypass anti-cheat or protected game processes.

## How to use it from Chat

Try:

> Find my running offline test game, verify the actual health value, and sample it over time using the memory helper. If there is a pointer candidate, re-check it on the attached process and explain whether it has been tested across restarts.

> For my own offline game, show the entities' coordinates using `read_entity_layout`. If you don't have an observed structure layout, say which exact offsets and types you need rather than guessing.

> In the disposable test process, find a unique numeric value and propose a verified health change. Ask my approval before changing memory, then read back the result.

The Chat workflow opens the memory helper when the request is recognized. The AI can propose tool calls but **cannot override macOS process-access permissions**.

## What was actually verified

- Python unit tests verify pointer-chain re-resolution, bounds, no unrequested layout fields, and typed entity decoding.
- The test fixture changes its own health and publishes its verified in-memory struct layout.
- On Linux, the complete test (external process attach, pointer read, entity read, live sample, write, target-side verification, restore) succeeded on **two separately spawned child processes**.
- The local HTTP radar returned data from the external-memory reader in a Linux test.
- Apple Silicon/macOS external-process access, Tauri compilation, and Cheat Engine control are **not tested here**. macOS `task_for_pid` can be denied even when the tool code is correct.

## Remaining engineering work

- Automatic discovery of undocumented entity coordinates, game-specific pointer anchors and module-relative offsets.
- Independent automatic discovery of stable module-relative RVA/chain values and regression testing across real game versions, without using the disposable fixture's published metadata.
- Screen-space world-to-camera transform and a game-window overlay for arbitrary games. The current radar is a separate browser view.
- General-purpose cheat generation, compilation, and test-oracle selection for arbitrary games; none is promised by this lab.
- macOS native build verification and a working integration against each specific user-supplied compatible game and tool.

**Do not disable System Integrity Protection or other host protections to make an incompatible process accessible.**
