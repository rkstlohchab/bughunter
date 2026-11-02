# BugHunterClaudex — AI red-team and shared learning update

## Added

- **Model red-team workspace** under the main navigation: durable test-case library, typed methods and tags, test-system instructions, outcomes and human-reviewed lessons, search, lineage/variants and JSON export.
- **Provider evaluations**: one bounded API inference per explicit run, no execution tools. Handles OpenAI Responses, Anthropic Messages, DeepSeek/OpenAI-compatible Chat Completions, on-device CrucibleLLM, Ollama and remote authenticated Ollama. Tests begin with an `untested` verdict, never inferred success.
- **History-informed drafts**: `redteam.suggest` passes a bounded sample of previous prompts, outcomes, responses and human lessons to the configured inference provider, asking for one new JSON candidate. It is not automatically executed or saved. Historical data is presented as untrusted evidence.
- **Reusable knowledge**: `redteam.insights` aggregates test history, and reviewed results may be published into the cited Knowledge Library. The main investigation agent already uses that library. Saved prompts, variants and metadata persist across app restarts in SQLite; this does not retrain model weights.
- **Separate system prompts**: global operator preferences and a new per-provider system-prompt map are appended to the main agent's built-in instructions. The red-team model under test has a separate, independently configurable test-system prompt. The CE bridge's prompt setting remains independent.
- **Read-only cross-workspace learning**: CE/mac bridge panel can retrieve relevant local knowledge and red-team experiment notes and append them to a user-initiated prompt as untrusted reference material. Automatic retrieval is an editable toggle. The model cannot issue backend RPC calls.

## Reliability fixes

- Kept bridge input in the editor when sending fails, instead of clearing it before an agent request returns.
- Explicitly separated trial records from verdicts; model outputs require operator review.
- Inputs, trial size, history context, API response size and API calls are bounded; remote model endpoints require configured keys/pricing and HTTPS except local endpoints.
- Added regression coverage for CRUD, review, search, history-aware suggestion, publication, system-prompt separation and API protocols. Fixed an Anthropic prompt reference error surfaced by the regression tests.

## Verification in this environment

- Backend focused tests: **44 passed**, using a stubbed keyring (environment has no `keyring` package). This includes red-team tests, system-prompt tests and existing local/remote/DeepSeek provider tests.
- macOS native memory MCP Python unittest suite: **6 passed**.
- `python -m compileall`: passed.
- TypeScript TSX syntax parse (global TypeScript): all changed TSX files parsed without errors.
- **Not validated:** full `tsc`/Vite or Playwright UI runs (Node dependencies unavailable and offline), complete Tauri/Rust compilation (Rust toolchain unavailable), live API-provider endpoints or actual CE/macOS desktop integration. No claim of live provider success.

## Preserved boundaries and gaps

- Existing per-call approval of state-changing work and the network gateway remain in place. No agent has acquired unrestricted process/machine access. The new model-testing workspace sends text to an explicitly selected provider, but no tools.
- The model provider may refuse individual prompts according to its own policies; the app cannot guarantee how an external model will respond.
- The CE bridge's JSON settings may still contain plaintext bridge/API tokens. This release does not migrate those secrets to an OS keychain; protect the config directory and implement keychain-backed storage in a subsequent, platform-tested release.
- Published Knowledge Library lessons are snapshots. Editing the original review later does not rewrite a previously published source; create a new reviewed snapshot for changed conclusions.
