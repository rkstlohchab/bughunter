# P7 — Production Integration & End-to-End Reliability

This release includes all prior P0–P6 source and adds a persistent local diagnostic service, operator-started smoke suites, a cross-model **read-only** readiness tool, and a Settings → Release readiness panel. The output ZIP remains `BugHunterClaudex.zip`.

## What P7 proves and what it does not

- **Preflight** detects installed CLI tools, checks source/module presence, and labels Mac, Burp, course, Tor, and memory workflows *not verified* until you perform the relevant real-world checks. It does not call paid models, website targets, Tor sites, or game processes.
- **Local smoke suites** run only fixed bundled scripts, when explicitly started by the desktop operator. Results are stored with timestamp, exit code, and bounded output. The application Stop action cancels its own suite. A crash or restart marks an unfinished suite interrupted, not successful.
- **Connection probes** for selected AI, local Burp bridge, Tor SOCKS and media runtime require a click. An AI provider configuration check is not evidence of the model successfully solving a task. A successful Tor SOCKS handshake does not verify any onion page.
- **AI awareness**: the `get_runtime_readiness` model tool returns coarse states and last test outcomes, not local paths, credentials, source text, or raw logs. Models cannot start tests through this tool.
- **Evidence**: `readiness.p7.export` writes a local JSON report at `<data directory>/exports/p7-release-readiness.json`, clearly stating that live Mac verification is separate.
- **Packaged app**: compiled Python modules are detected by import; source-based smoke scripts are disabled if the source tree is not bundled. Run the extracted project's script for release checks. This prevents a misleading Run button inside the `.app`.

This is an integration and diagnostic milestone. It does not silently finish arbitrary-game ESP, universal нестандартный-ввод, production checkout срабатывание, CAPTCHA solving, autonomous arbitrary command execution, or model fine-tuning.

## On your Mac: final test sequence

Unpack the same fixed-name ZIP and install/build:

```bash
cd ~/Downloads
unzip -o BugHunterClaudex.zip
cd bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p7_release.sh --quick
bash scripts/verify_p7_release.sh --full
```

`--quick` runs Python readiness tests and, after npm dependencies are present, the frontend routing test and frontend build. `--full` adds the P0–P6 regression suite, the disposable checkout suite, read-only pointer tracking on two spawned test processes, and on macOS the Accessibility helper build, `cargo check`, Burp bridge build, complete Tauri build, and packaged-app smoke check. These may take time; logs show skipped checks rather than labeling them passed.

### Final acceptance checklist (live checks, outside automated fixtures)

1. **Native Mac app** — launch the built `.app` on Apple Silicon; verify Settings, Knowledge, and Chat, then close/reopen and confirm data persisted.
2. **Chat → computer** — grant macOS Accessibility; ask Chat to open a disposable text editor and enter a distinctive text; observe native tool receipts and verify actual application state.
3. **AI providers** — save and test each configured local Ollama, Kaggle/OpenAI-compatible, and subscription provider independently. Do not infer one provider is connected from another provider's results.
4. **Courses** — import your original course ZIP, process real Whisper audio and Qwen3-VL frames, inspect exact timestamps/citations, create a P1 fixture experiment, and verify reviewed P6 skill recall after restarting the app.
5. **Burp** — build/load the extension in Burp Professional, use P7 Burp probe, import an in-scope proxy capture, and confirm history and Scanner finding metadata matches the actual extension response.
6. **Память-процесса-И** — first run the bundled read-only fixture; then verify the native мост-ИП against the intended accessible offline game. Fixture addresses do **not** validate arbitrary third-party game offsets.
7. **Tor** — use an operator-bookmarked research page through the actual Tor browser; confirm stop/pause at login/CAPTCHA, manually continue, and capture an unreviewed page. No automatic CAPTCHA-solving claim.
8. **Research & knowledge** — monitor a subscribed HTTPS feed, review the result, inspect the knowledge graph, and verify no unreviewed source is automatically promoted into a verified skill or training dataset.
9. **Persistence and failure handling** — stop a running P7 suite, restart the app mid-task, confirm interrupted status and local evidence export, and verify emergency stop cancels local test runners.

**Record actual Mac results** in a separate operator test log. A P7 evidence export only contains checks the app itself observed, and is not an attestation of unrelated target compatibility.

## RPC endpoints

| RPC | Operation |
| --- | --- |
| `readiness.p7.preflight` | Read-only preflight, no network calls |
| `readiness.p7.runs` | Read local suite history |
| `readiness.p7.start` | Start one fixed suite; `suite` and `approved: true` required |
| `readiness.p7.stop` | Cancel active suite; `id` and `approved: true` required |
| `readiness.p7.probe` | Operator-approved model/Burp/Tor/media check |
| `readiness.p7.probes` | Return bounded sanitized probe history |
| `readiness.p7.export` | Write local evidence report |

## Fixed smoke suites

- `learning` → `scripts/verify_p6_learning.sh` (P0–P6)
- `web_lab` → `scripts/verify_staging_quote_sandbox.sh` (disposable checkout)
- `memory_lab` → `scripts/verify_game_memory_restarts.py` (read-only, two spawned process instances)
- `chat_routing` → `tests/unit/task-intent.test.mjs` (natural-language routing)

Suites never accept user-supplied commands, targets, scripts, or extra flags. Commands saved in tutorials or from unreviewed research are never executed by P7.

## Verification performed while packaging

See the release response for the exact test counts and environment-specific skips. The Linux build environment cannot compile the Mac application or prove native Accessibility, actual Burp Professional connectivity, real model inference, original course mastery, or Tor browser interaction. CI's macOS release job and the final acceptance checks above remain the release gate.
