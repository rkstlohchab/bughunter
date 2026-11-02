# Task execution and recovery update

## Changes

- **Tor Browser**: Opening a saved source now creates a durable `tor_browser` task. Browser navigation, link following, page capture, failure and close operations are recorded at the backend execution boundary. Saved capture IDs are linked; URL query strings and response bodies are excluded from the activity log. A failed navigation remains visible and does not destroy a still-running browser session.
- **Memory bridge**: Starting a memory-agent or manual Lua/ping operation in the desktop workspace creates a `memory_session` task. The UI records tool-call names, results, approval prompts and outcomes received from the Rust MCP bridge, without persisting tool arguments, raw memory contents or API credentials. The memory bridge itself still enforces its approval requirements.
- **Recovery**: Incomplete Tor collection, Tor Browser and memory-session records are marked **interrupted** after a restart. Events already saved remain visible. No browser or memory operation is replayed on restart. Clean Tor Browser shutdown records a closed session.
- **Chat validation**: Invalid/unindexed attached documents are reported before provider credentials are checked, so document errors do not misleadingly appear as API-key failures.
- **Agents tab**: Added browser and memory task types and status labels. Live tools must be stopped from their own workspace; the history view cannot force a remote rollback.

## Verification in the container

- Unit/integration tests cover successful browser sessions, navigation failures, capture references, restart recovery, rejection of raw memory parameters and forbidden replay.
- A real local Python backend was exercised over stdio in two separate processes (`scripts/smoke_task_recovery.py --python`) using a temporary data directory and a synthetic memory-tool event. No external process was inspected.
- TypeScript syntax validation used the installed TypeScript parser. It is **not** a full Tauri or browser UI build.

## On a macOS development machine

```sh
cd ~/Downloads/bughunter
python3 scripts/setup.py
python3 scripts/build_backend.py
backend/.venv/bin/python scripts/smoke_task_recovery.py
npm run desktop:dev
```

After the desktop app launches, open a saved Tor source or start a memory-agent task. Check **Agents** for its observed operations, approval state, error and completion status. Test a mid-task app restart, verify the previous session reads **Interrupted**, then explicitly reconnect the tool before starting fresh work.

## Production limitations

- The macOS Tauri bridge and Tor Browser/geckodriver were not launched in this container, so platform-specific execution, window behavior and browser routing need real-device tests.
- The memory run is tagged `desktop_reported` because its tool executor is in Rust, not the Python task service. The backend refuses raw arguments/outputs but depends on the desktop integration to send its event notifications.
- API/GUI end-to-end tests, authenticated live model inference, structured cancellation of every external tool, and multi-project isolation still require a dedicated production validation pass.
