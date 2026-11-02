# BugHunterClaudex — reliability review (2026-10-31)

## Changes in this archive

- **Bridge custom instructions:** `effective_system_prompt()` always retains the backend's built-in operating instructions and appends at most 12,000 characters of user preferences. The UI explains this and enforces the same length.
- **Recoverable model errors:** model failures leave the current agent session parked, without replaying earlier tool calls. The desktop provides `ce_retry_agent`, Retry, and Cancel controls. Failed approval requests do not silently dismiss the dialog.
- **Tool-call validation:** malformed or non-object arguments, missing names, and batches larger than 24 tool calls are rejected before execution. Operator prompts are bounded to 20,000 characters.
- **Connection consistency:** changing backend, Python binary, script path, CE host/port, or CE token requires disconnection before saving.
- **Approval classification:** detach and clear-scan operations now need approval when model-proposed (they mutate process attachment or scan state). Human-initiated clicks remain direct.
- **Knowledge headings:** Markdown headings accompany later chunks as `section` metadata and in the FTS title. Stored passage and quotation text are not altered. Existing passages without the section metadata remain usable; reprocessing them picks it up.
- **Coverage:** Rust unit checks for prompt preservation and malformed tool calls and UI regression specs for retry and approval errors were added. They are included for use on a machine with the desktop toolchain.

## Verification performed here

- `python -m pytest backend/tests/test_knowledge_retrieval.py backend/tests/test_resource_imports.py backend/tests/test_policy.py backend/tests/test_traffic.py backend/tests/test_lab.py -q` — **63 passed**.
- `python -m compileall -q backend/bughunter` — **passed**.
- TypeScript syntactic transpilation of changed `src/*.ts(x)` and `tests/ui/ce-bridge.spec.ts` — **passed**.
- Full Rust/Tauri compile/test — **not run**: `cargo` and Rust toolchain not installed.
- Full frontend typecheck, Vite build, Playwright UI tests — **not run**: npm dependencies not installed; remote package installation unavailable.
- Complete Python suite — **not run**: optional/required environment packages `keyring`, `mcp`, and `sqlite-vec` are missing. A separate baseline knowledge-suite test also needs `sqlite-vec`.
- macOS and Windows bridge integrations — **not run**: require corresponding desktop environments and local target/test server.

## Remaining issues to prioritize

1. **Plaintext provider keys and bridge tokens** in the app config file. Move secrets to an OS credential store and migrate existing keys, with explicit backup and recovery behavior. Current Unix file mode reduces exposure but does not provide encryption.
2. **Lua denylist is not a sandbox.** Obfuscated code can circumvent string matching. Continue to require exact operator approval; longer term, narrow the model's available tools and use a stricter execution boundary.
3. **Real-device and full-toolchain regression coverage.** Build and run on Windows and macOS with the intended MCP servers; test the retry flow on model timeouts, malformed tool calls, disconnection, and backend switching.
4. **Search evaluation corpus.** Query aliases use broad OR expansions that may introduce false-positive matches. Build a labeled query set and measure precision/recall, especially on larger libraries.

This release makes no claim that the full app compiles or runs on Windows/macOS without the tests above.
