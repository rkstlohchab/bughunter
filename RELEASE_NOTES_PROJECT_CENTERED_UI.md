# BugHunterClaudex — Project-centered UI update

## Navigation
- Four main destinations: Chat, Workspaces, Knowledge, Settings.
- Workspaces retain Web testing, Cheat Engine/macOS memory, AI testing and Tor research. Web-testing secondary actions are simplified, with less-used actions behind More tools.
- A project selector is available in the global top bar. New projects can be research-only, with no web target. Web testing still requires a defined authorized target before actions run.
- Newly created AI test cases, Tor bookmarks, and specialist training collections can carry the active project. Legacy unassigned records remain accessible.

## Settings
- Provider choices are separated into Subscription, Paid APIs, and My own models (including the Kaggle llama.cpp OpenAI-compatible endpoint).
- API budget and token prices are shown only for paid API providers. Matching backend budget/preflight checks for self-hosted models were corrected, not merely hidden.
- One editable shared AI prompt now applies to Chat and the main investigation agent. On connecting the desktop memory bridge, the same prompt is synchronized to its configuration. The bridge retains its built-in rules. The red-team target-system prompt is intentionally independent: it is part of the test case, not the user's agent instructions.
- Key storage now has a persistent Key saved in keychain indicator. Password bytes remain hidden after storage; backend exposes only the presence boolean.
- Tool connection actions open a centered guide with context and scoped actions, instead of showing all connection fields on one screen.
- Local embedding models and transcription settings are inside Optional technical settings; neither is required for a remote reasoning endpoint. Search falls back to keyword retrieval.
- Notifications float over the app rather than occupying space in forms.

## Knowledge and learning
- Main view has search and Upload / add link. Knowledge graph is a visible top-level subview. Script drafts, exports, training datasets and low-level details stay available behind Advanced.
- Added a bounded public YouTube playlist/channel importer: up to 50 videos per import, queued as individual transcript resources with citation provenance. Private/login-only content and bulk Instagram profile ingestion remain unsupported. Local video, audio, ZIP, course folders and direct public pages retain existing import routes.
- Chat now retrieves relevant cited Knowledge Library passages and up to five matching reviewed specialist notes across Cheat Engine, AI testing, Tor research, and training examples. Unreviewed training drafts and unreviewed Tor captures are excluded. This is retrieval, not training of model weights. Chat remains conversational and does not execute tools.

## Verification
- 22 selected Python regression tests passed (keyring stub in test runner because keyring is unavailable in this container).
- All TSX source files parsed with TypeScript 5.8.3. Python source compilation succeeded.
- Full React/Vite/Tauri build and macOS live integration were not run here: npm packages could not be installed from this environment.
- Existing approval, scope and isolation requirements for web requests were kept.

## Known limitations
- Project-linked specialist content uses project tags for new records. Legacy records remain visible. Knowledge Library is intentionally shared, and not every legacy subsystem enforces hard per-project storage isolation.
- AI Chat uses retrieved knowledge but is not an autonomous tool executor. Workspaces retain tool execution and approval handling.
- YouTube collection import processes only the first 50 public video entries on a single request. Whole-channel pagination and authenticated Instagram profile media ingestion are not implemented.
- Local speech transcription requires locally installed media dependencies and a suitable whisper.cpp model.
- Memory-bridge shared prompt sync happens at Connect time; reconnect after changing the shared prompt.

## Upgrade on macOS

Extract this archive to a fresh folder, keep a backup of your original project and app data, and run from its `bughunter` directory:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

The bundled backend must be rebuilt whenever backend Python files change.
