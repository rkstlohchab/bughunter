# BugHunterClaudex — Unified task execution and model prompt corpus

## Changes

- Added a persistent, single-view `TaskService` with task creation/listing/details, linked real runner status, scoped tasks, conservative retries and stop/recovery behavior.
- Routed new Web Testing and Knowledge Study tasks through the shared ledger; the Chat-to-CLI delegation also uses it. The Agents tab now shows the shared activity and approval state.
- Routed bounded Tor research capture through the ledger and added cancellation checks between page requests, retaining partial captures.
- Bundled the user-supplied `abusegpt.zip` without executing it. Indexed model-attribution hints, candidate methods, original archive locators, SHA-256 duplicates and screenshot associations.
- Added a model-specific research explorer in AI Testing, including screenshot viewing, correcting evidence associations and human review outcomes.
- Added separate per-model descriptive documents to Knowledge. Prompt text and success claims are excluded from automatic shared-chat context.
- Included the prompt corpus and summaries in the macOS/Windows PyInstaller sidecar packaging script.

## Evidence summary (this archive)

- 277 bounded text/Markdown records, including documents from nested ZIPs
- 10 PNG screenshots, all with a suggested (not verified) association
- 216 other attachments, never executed
- 42 exact content duplicates retained with references
- 11 model-family filters (including **Unspecified**), with several multi-model entries
- Nine candidate taxonomy categories; counts are keyword matches, not effectiveness measurements

## Verification

- 252 backend tests passed, 6 skipped in a local test run using a temporary in-memory `keyring` test double because the actual keyring package was unavailable.
- `test_prompt_corpus.py` validates nested-archive indexing, model attribution, screenshot linking, human review and non-execution of script attachments.
- `test_unified_tasks.py` validates unified records for coding-agent, web, study, memory-handoff and Tor tasks.
- All TS/TSX source files parsed without syntax errors using TypeScript 5.8.3.
- The desktop build, Electron/Tauri UI rendering, live tool integrations, credential store and real model-provider connectivity were **not** verified here.

## Install / data

Extract to a new `bughunter` folder and back up the existing user-data folder before updating. From the project folder on a Mac:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

Model prompt evidence appears under **Workspaces → AI Testing → Model-specific prompt research**. Model summaries appear in **Knowledge** after startup indexing completes.
