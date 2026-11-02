# BugHunterClaudex — Native Mac Tool Handoff (31 October 2026)

This is an **incremental source-code release**, not a completed autonomous desktop agent or a signed `.app`.

## What actually changed

1. **Native Mac app handoff in Settings:** Recognized Burp Suite and Cheat Engine `.app` bundles now have a **Check running app** action and a user-clicked **Open app** action. The Rust implementation independently validates the app bundle and its `Info.plist` identity; the backend's installation claim is not sufficient authorization to run arbitrary files. It invokes only macOS's `/usr/bin/open -a` with a validated bundle path; no shell, model agent, script execution, game-memory access, or general computer control is introduced.
2. **Clearer connection states:** The UI differentiates installed, running/visible, and *not connected or verified*. A successful `open` command merely means macOS accepted an app-open request.
3. **Loopback-only Burp listener check:** Clicking **Check local proxy port** checks TCP connectivity to `127.0.0.1` on the configured proxy port. It never sends HTTP payloads, does not reach an external host, and explicitly says that a responsive port is **not** proof Burp Suite is running or authenticated. The separate gateway and Burp MCP connection checks remain necessary for actual workflows.
4. **Windows Cheat Engine clarity:** When the detected Cheat Engine is a Windows `.exe`, Settings explains that it needs Wine/CrossOver or Windows rather than advertising a native `.app` launch. The native Mac memory bridge is separate from Cheat Engine.
5. **APK evidence regression fix:** The `dex_files` field again records the number of DEX files as expected by existing consumers; `dex_paths` retains the bounded list of observed filenames.
6. **Maintained architecture boundary:** These Mac handoff commands are Tauri UI commands initiated by a person in Settings. They are not tools available to the investigation agent, Python backend, knowledge documents or website auditors. This prevents a retrieved webpage or file from causing arbitrary desktop activity.

## Use it

Unzip into a new directory rather than overwriting your existing installation without backup. Open the app, go to **Settings → Optional tools**, select/locate Burp Suite or Cheat Engine, then click **Open app** or **Check running app**. If Burp's proxy is set to `http://127.0.0.1:8080`, **Check local proxy port** tells you whether *a listener* responds. Use the separate Burp connection flow to prove actual functionality.

```bash
cd ~/Downloads
unzip -o BugHunterClaudex-Mac-Native-Tool-Handoff-2026-10-31.zip
cd bughunter
python3 scripts/setup.py
npm run desktop:dev
```

## Tests actually performed in this build environment

- `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_mac_tool_handoff_inventory.py backend/tests/test_tool_readiness_accuracy.py backend/tests/test_crossover_discovery.py backend/tests/test_autopilot_chat_first.py tests/test_autopilot_evidence.py`: **20 passed**.
- `python3 -m unittest discover -s tests/mac_mcp -q`: **6 passed**.
- Python source `compileall`: passed.
- TypeScript/TSX syntax parsing of **28 files** using TypeScript's transpiler: **0 syntax errors**. This is **not** equivalent to `tsc` typechecking or a Vite build.
- Full backend test collection **blocked** here by missing Python dependency `keyring`.
- `npm ci`: unable to complete network dependency installation in this environment.
- No Rust/Cargo toolchain is installed in this environment; **macOS native code has not been compiled or tested on a Mac**.

## Important limitations and next checks

- This build does **not** permit the AI to drive arbitrary applications or control the whole computer. It supplies a narrow, operator-initiated app launch and evidence-backed connection diagnostics.
- It does **not** provide a working Cheat Engine automation API, autonomous game modifications, unrestricted scanning, or complete Burp Suite Professional API automation.
- Mac users should verify app-bundle identity checks against their installed Burp/CE version and report any mismatch; a known named `.app` is not automatically accepted without metadata validation.
- For real website testing, target authorization, scope, the local gateway, and any required approvals remain in force.
- Back up your data before updating; user data is not included in this source archive.
