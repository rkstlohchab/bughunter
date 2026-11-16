# Claude P04–P06 implementation review and repair handoff

Review date: 2026-11-15. Repair author: Codex. This is an implementation repair record, not independent acceptance of the repaired revision.

## Current result

The reviewed implementation contained defects beyond the earlier type/module-base issues. Repairs are in the working tree. Targeted Python and Rust tests pass; the exact commands, logs and changed-file hashes are under `artifacts/game-verification/game-runtime-v1/codex-P05-P06-review-001/`.

**P05/P06 are not complete app features.** The main chat still exposes `game_targets`, `game_session`, `game_fixture`, `game_read`, `game_write`, `game_status`, and `game_stop`. Discovery libraries and feature verification CLIs are not wired through the durable native executor as scan/feature tools. Status now explicitly reports those unavailable capabilities, and the runtime prompt no longer implies that running a standalone script is controlled by app Stop.

**Live Among Us is blocked on this host.** Codex independently discovered/measured the currently running executable and attempted attachment without writing memory. PID 18851, birth `1794722854412539000`, executable SHA-256 `3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992`. LLDB returned `Not allowed to attach to process`; see `live-readonly/summary.json` and `live-readonly.log`. This establishes an attach denial, not a proven explanation of every macOS entitlement or a tested workaround. No game signature, system protection setting or installation was changed.

## Reproduced defects and repairs

- **Debugger paused the target for the entire session.** Persistent LLDB now briefly stops for each operation and resumes before returning the receipt, including error paths. Native test targets can service their independent control channels while attached. An already-paused target is rejected before attach without changing its state. A timed-out debugger session is poisoned rather than reused for another write. Tiny stop/resume settling intervals are part of the current implementation; this is tested on this host, not a universal LLDB performance guarantee.
- **Suppressed lifetime coverage.** Re-enabled both previously skipped Python native lifecycle tests and the ignored Rust independent external-process test. Fixture stdout/stderr drains are interruptible; shutdown joins readers before closing buffered pipes, avoiding a deadlock. Cooperative fixture shutdown is attempted while the debugger is attached. Zombie processes no longer qualify as live process identities. Tool process-group shutdown handles a Darwin EPERM race by checking group absence rather than assuming termination.
- **Unknown write outcomes were misclassified.** A structured native write error is `outcome_unknown` unless a failed compare proves no write occurred. A separate Rust test covers this distinction.
- **Scanning lost matches and overstated completion.** Scans preserve cross-chunk scalar/pattern matches, report short/failed reads as partial, continue to later chunks, retain cancellation, reset prior scan state, and update refinement baselines. Unknown-initial data stays on disk instead of building an unbounded dictionary for every byte position. Candidate memory is conservatively bounded; overflow is explicit rather than silently dropping results. Checkpoints now persist coverage information. These are coverage checkpoints, not a claim of full crash-resume implementation.
- **Pointer discovery read entire regions.** Searches now read bounded chunks, cap depth/time/candidate expansion, support cancellation, expose incomplete coverage, and do not fabricate a zero-hop absolute recipe when no pointer was found. Resolution rejects stale recipes, null pointers, invalid module bases, nonfinite values, and unbound/cross-process absolute roots.
- **Mapping history was not an immutable recipe history.** History includes the prior full contract and observations. Root and build can be revised. Concurrent stale updates are rejected. Restart promotion rejects evidence from a different revision, stale recipes, absolute roots and nonfinite/missing values.
- **Migration changed pointer meaning and could partly commit.** Integer offsets and legacy value types are normalized; add-before-dereference semantics are preserved. Conflicting nested/flat offsets are rejected. A migration batch is one transaction, including mapping events; failures roll back the batch. Backups have unique names and restoration uses SQLite's backup API rather than copying over a WAL database. Original legacy IDs remain in evidence IDs and legacy rows remain intact.
- **Features could write after Stop and hide failed cleanup.** Feature transitions now reject writes before start/after stop, serialize mutation against Stop, pin the resolved object path, require conditional writes, normalize scalar values, and report failed cleanup truthfully. `maintain()` provides one explicit repeated-write tick; it does not replay failed writes. A runtime validation callback is available for game-specific role/round/death checks. Those checks still need real game integration and evidence.
- **Candidate identification was not identity verification.** Constant zero/positive values and unreadable samples no longer count as countdown evidence. Candidate scans are bounded and diagnostic; the CLI does not select the first candidate and write to it. The field layout is restricted to its measured build. A valid LocalPlayer module-relative root must still be derived; `LocalPlayer +0x0` and `killTimer +0xB0` do not establish that root.
- **Verifier reports could claim success without the advertised behavior.** Live executable fingerprinting uses the selected PID's image, not a hard-coded installation path. Actual loaded module bases are required. All exit paths close the adapter/fixture; attach denial creates a structured blocked report. Fixture countdown is correctly treated as `u32`, with 60 → 0 → target-driven reset to 60 observed. Live success remains `pending_gameplay_review`; no script fills human gameplay attestations. Existing report directories are not overwritten.
- **Restart verification trusted a supplied hash and could hang on a silent launcher.** The launcher has a bounded nonblocking readiness protocol and guaranteed cleanup. Arguments use shell-aware tokenization without invoking a shell. Each real process's executable is measured; `--build-fingerprint` is an assertion, never an override. The same saved module-relative recipe is resolved over distinct births and its exact revision is bound to the observations. `open -W` is not a valid readiness-protocol launcher.
- **Native type aliases were not exposed consistently.** The game read tool schema and service validation now agree with the adapter's shared-contract aliases (`f32`, `u32`, etc.).

`reproduced-before.json` reruns three narrow reproductions against the captured original sources: missed cross-chunk match, obsolete revision accepted for restart verification, and a write accepted after Stop. Each occurred before repair and has a rejecting/passing regression in the repaired tree.

## Verification

- `final-python-2.log`: 86 passed, no skipped tests, including the new review regressions and native adapter/lifecycle tests.
- `prerequisites-2.log`: 204 passed across contracts, fixture, verifier, knowledge, target qualification, broker and service/native tests.
- `harness-legacy.log`: 27 passed for harness modes/routing and legacy memory lifecycle.
- `final-rust-2.log`: 48 passed, zero ignored. Includes real helper external-child read/write/reset, durable chat/native cleanup, and unknown-write receipt classification.
- `feature-fixture/summary.json`: standalone native fixture baseline 60, enabled 0, target-driven reset 60; verdict `fixture_pass`, `gameplay_verified=false`.
- `profile-restarts-fixture/report.json`: one saved recipe resolves to 60 across three distinct fixture process births, then is promoted to `restart_verified` within the isolated fixture store. This does not promote any Among Us recipe.
- `live-readonly/summary.json`: real target attach blocked, exit 2; no gameplay verification or memory write.
- Original failures and intermediate debugging logs are retained. Full backend-suite baseline was not rerun; the historical 49-failure comparison is not claimed for this repaired revision. No UI redesign, package/release build, Windows validation, or actual game behavior is claimed.

The final fixture CLI replay also covers the subsequent stricter handshake check. `final-affected.log` records 109 passing targeted checks after cleanup of script documentation and manifest source dependencies; these overlap the counts above and are not additional unique tests.

## Files changed

Runtime and discovery:

- `backend/bughunter/game_runtime/among_us.py`
- `backend/bughunter/game_runtime/features.py`
- `backend/bughunter/game_runtime/live_target.py` (new)
- `backend/bughunter/game_runtime/mappings.py`
- `backend/bughunter/game_runtime/migrations.py`
- `backend/bughunter/game_runtime/pointers.py`
- `backend/bughunter/game_runtime/scans.py`
- `backend/bughunter/game_runtime/service.py`
- `backend/bughunter/game_runtime/testing/fixture_target.py`
- `backend/bughunter/memory_engine/mac_adapter.py`
- `backend/bughunter/memory_engine/protocol.py`
- `backend/bughunter/memory_engine/server.py`
- `backend/bughunter/operator_tools.py`
- `src-tauri/src/game_executor.rs`
- `src-tauri/src/game_bridge.rs` (restored ignored test)

Verification and handoff:

- `backend/tests/test_game_runtime_features.py`
- `backend/tests/test_memory_engine.py`
- `backend/tests/test_game_runtime_review_regressions.py` (new)
- `scripts/verify_game_feature.py`
- `scripts/verify_game_profile_restarts.py`
- `docs/game-lab/phase-manifest.json`
- `docs/game-lab/P06-live-launch-instructions.md`
- `docs/game-lab/P05-P06-codex-review.md` (this file)

The exact per-file hashes, patch, source copies and verified temporary-tree rollback are in the review artifact directory. No commit was created. Unrelated pre-existing dirty files and the `codex-selection-feedback` claim were preserved.

## What Claude should do next

1. Read this handoff and the measured blocked target result. Do not ask the user for a PID or assume enabling computer control fixes native memory access. The operator wants to supervise, not manually perform technical steps.
2. Complete P05/P06 integration through the existing durable broker, private Rust executor and native helper. Add bounded scan/refine/candidate receipts, mapping save/revalidation, and feature start/monitor/stop as task-owned operations. App Stop, task Stop, helper exit and app-close must cover these operations. Do not start a detached CLI and label it integrated.
3. Add game-specific local-mode/role/round/player/death guards backed by measured fields and a measured LocalPlayer root. The generic feature path now checks process birth and resolved addresses; it cannot infer a same-address object reuse or a role change without those observations. If target access remains denied, record that gate as blocked and finish the independent integration work against the disposable fixture.
4. Make the scan checkpoints actually resumable if claiming crash-resume completion; add mapping import/export with status reset if that planned surface is still required. Do not claim features that only have a library API as completed chat capabilities.
5. Replay the app flow with an actual chosen model and computer-control tools, foreground input allowed. Automate the local game actions and record captures where possible. Ask only for a real OS prompt or inaccessible action. Human review remains an operator observation, not an agent-filled checkbox.
6. Prepare fresh source-bound reports for P00, P03, P04, P05 and P06. The manifest now includes the new shared live-target helper and regression suite, and P05/P06 are `working` rather than implying a finished phase. P01/P02 source was not changed in this review, but dependent/frozen-manifest bindings must be refreshed. Preserve all historical run directories and reviewer identities.
7. Arrange a reviewer who did not change the final implementation. Codex authored these repairs and has not set reviewer acceptance. The current phase verifier correctly rejects historical report hashes against the changed sources; an author cannot fix this by marking stale evidence verified.

## Replay commands

From `backend/`:

```sh
uv run pytest -q --timeout=45 tests/test_game_runtime_features.py tests/test_game_runtime_scans_pointers.py tests/test_game_runtime_review_regressions.py tests/test_memory_engine.py
uv run pytest -q --timeout=45 tests/test_game_runtime_contracts.py tests/test_game_runtime_fixture.py tests/test_game_runtime_broker.py tests/test_game_runtime_service.py tests/test_game_runtime_native.py tests/test_game_runtime_knowledge.py tests/test_game_runtime_targets.py tests/test_verify_game_plan.py tests/test_baseline_report.py
uv run pytest -q --timeout=45 tests/test_harness.py tests/test_harness_modes.py tests/test_mac_memory_lifecycle.py
```

From the repository root:

```sh
cargo test --manifest-path src-tauri/Cargo.toml --lib -- --test-threads=1
```

`pytest-timeout` is available in this workspace; omit `--timeout=45` in an environment without that optional plugin, or install the same test tooling explicitly. The standalone commands and their exact arguments/exit codes are in `cli-results.json`.
