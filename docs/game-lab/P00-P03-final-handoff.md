# P00–P03 repair handoff → P04

Date: 2026-11-14. Workspace: `/Users/raksithlochabb/Downloads/bughunter`.

## Start here, Claude

Continue the game runtime work in this workspace. Codex used GPT-6.1 Sol implementation agents, inspected their changes, repaired additional issues, and replayed the finished checks. The separate acceptance reviewer exhausted its usage allowance before replaying the final frozen sources. Therefore **P00–P03 are `ready_for_review`, not `verified`**. Do not reuse the previous acceptance claims.

First independently replay the acceptance package below. Accept P00, then refresh the dependent reports' exact P00 report hashes and accept P01/P03 on their measured lanes. P02's static investigation is complete on macOS; its game-memory capability remains blocked. Then implement P04 on macOS and present its chat/Stop/recovery flow for the user's first human review. Do not ask the user to repeat permission to implement P04. Keep the website behavior and chosen model/provider intact.

No commits were made. Much of Claude's earlier implementation is still untracked. Preserve it and the historical evidence. The artifact directory is ignored by Git: copy it explicitly if handing this work to another machine.

## What is working and what that proves

- **P00:** strict shared records and cross-language fixture parity, an external-process oracle, and a verifier that rejects stale hashes, unreviewed evidence, incorrect prerequisites and cross-lane milestone claims. It now rejects inherited JavaScript properties such as `constructor` and `__proto__`. Phase source inventories are explicit: changing P01/P02 implementation no longer changes P00's source snapshot merely because the files share a directory.
- **P01:** persisted, cited knowledge drafts now participate in GameLab chat start and follow-up. Binding a collection replaces the previous binding; citations cannot fabricate private or stale content. Source changes invalidate earlier plans and corrections. The synthetic benchmark measured **30/30** expected passages in the top five, with **10 separate negative cases** covered by tests. These expectations were supplied with the implementation and still need reviewer scrutiny.
- **Actual course replay:** an isolated copy of the user's **17 imported sources / 597 active chunks** returned five cited passages for each of three queries about cooldown, changed-value scans and saved pointers. An unlinked session could not retrieve that private course. This proves retrieval and changed draft steps; it does not prove trained weights, perfect understanding, exact current-build pointers or changed gameplay.
- **P02:** executable/module/metadata/version-source hashes are measured from files. Mach-O and PE architectures are measured rather than trusted from JSON. A forged but well-formed SHA-256 string cannot qualify a target. The installed wrapped Among Us **2026.11.3, arm64, Unity 2022.3.62f3** produced **12,899 static files** through pinned Cpp2IL; a second invocation reproduced every file hash. REA's configured live catalog was queried. No AmongUs process was running, so `target_memory_access` remains blocked with zero bytes read.
- **P03:** real private helper entrypoints, bounded transport, process-birth checks, acknowledged writes, and OS-backed ownership shared with the old Mac panel. The Rust bridge read 60, wrote and observed 7, then restored and observed 60 in a separate process. Legacy scanning/module queries still work. Failed attach and detach release resources. Tool readiness requires an observed signal. Stop now handles a child that remains after its parent exits and ignores TERM. Rust progress notifications have an installable handler and an interleaving/disconnect regression test.

Windows implementations were inspected and strengthened, but **no live Windows run was available**. Android was not qualified. Mac fixture access is not Among Us access. No real-game memory write or gameplay change occurred in this pass. The source CLI helper was exercised; a newly built signed app bundle was not launched.

## Verification recorded by Codex

Evidence base: `artifacts/game-verification/game-runtime-v1/codex-P00-P03-repair-001/`.

- Focused backend checks: **224 passed**, zero skipped: contracts/schema/TS wrapper 51; verifier 75; baseline integrity 5; fixture 17; knowledge/integration 23; target qualification 22; native/lifecycle 31.
- Full Rust suite: **45 passed**, including eight bridge tests and six shared-contract tests. Only the three existing warnings in `execution_control.rs` and `mac_desktop.rs` remain.
- Node validator: **7 passed**. Routing: **42 passed**. TypeScript compilation: exit 0.
- Standalone legacy Mac panel: **21 passed, four skipped**. The skips concern direct Mach access; they are not counted toward native helper acceptance.
- Full backend suite: **1,236 passed, 49 failed, one skipped**. All 49 failing test IDs match the recorded pre-change baseline; no new failing IDs. Details and comparison are in `final/backend-full.json` and `final/backend-full.log`. Do not describe the repository's whole suite as green, and do not blanket-skip these failures for `P04.routing_regression`.
- Fresh verifier calls for P00, P01, P02 and P03 each exit **2**, with no invalid/stale evidence errors. They correctly report missing independent acceptance; dependent phases also report unaccepted P00. `final/phase-verification.json` records the commands and log hashes.

The commands and SHA-256 log hashes are in `final/commands-core.json`, `final/native-replay/commands.json`, `final/rust-bridge-final.json` and `final/qualification/replay.json`. Two report/summary wrapper mistakes were corrected without changing production code or inventing results; the initial verifier output is retained under `final/generation-attempt-001/`.

Current reports:

- `P00/run-003/report.json`: revision `sha256:3576e475c08e39d7e7536e814023c961ee1a39afe1b875c2a7b32188eb01c1e5`.
- `P01/run-002/report.json`: revision `sha256:c620b4e32df7f2e9ffb842b5df069eafbb19185de5d53f908501dc19d3cc7b5a`.
- `P02/run-002/report.json`: revision `sha256:a73ab1f61d34194078c15fb6d303f6b47e8018712391a9e14339ba672db03406`.
- `P03/run-002/report.json`: revision `sha256:9eaadb32f7e51c73d6c5c5a4c00c08d404bcf9e8638692f5254c605670e8d6fa`.

All report paths above are relative to `artifacts/game-verification/game-runtime-v1/`. Each report binds actual source files, an immutable manifest copy, executed commands and hashed evidence. Leave `reviewer`, `reviewed_revision` and `independent_replay` null until the replay really occurs.

## Independent acceptance replay

Use a fresh shell and a new output directory. This runs every focused check sequentially so debugger-backed native tests do not overlap:

```sh
cd /Users/raksithlochabb/Downloads/bughunter
python3 artifacts/game-verification/game-runtime-v1/codex-P00-P03-repair-001/final/replay_all.py \
  --output artifacts/game-verification/game-runtime-v1/claude-P00-P03-replay-001
cargo test --locked --manifest-path src-tauri/Cargo.toml -- --test-threads=1
```

The script refuses an existing output directory and writes `commands.json` plus hashed logs. Capture the additional full Rust command to your own log. Review the negative fixtures, verifier milestone cases and source inventories, not just the passing count. Inspect the knowledge benchmark expectations before accepting retrieval accuracy.

Reproduce the actual static derivation with the **original** pinned asset; the CLI creates and signs its own isolated copy. A fresh report directory is required because `static-analysis/` must not already exist:

```sh
mkdir artifacts/game-verification/game-runtime-v1/claude-P02-static-replay-001
cd backend
uv run python ../scripts/qualify_game_target.py \
  --target ../artifacts/game-verification/game-runtime-v1/codex-P00-P03-repair-001/target.json \
  --report-dir ../artifacts/game-verification/game-runtime-v1/claude-P02-static-replay-001 \
  --cpp2il ../artifacts/game-verification/game-runtime-v1/codex-P00-P03-repair-001/tools/Cpp2IL-2022.1.0-pre-release.21-OSX-ARM64 \
  --rea-catalog --pretty
```

Compare the new report's output path→hash map with `codex-P00-P03-repair-001/final/qualification/target_qualification.json`, and rehash the output files themselves. Revalidate installed target bytes; a game update must invalidate the old target evidence. A missing REA installation or changed game is a new measured limitation, not a reason to copy this run's claims.

For each report that you actually reproduce, record a distinct reviewer identity, the exact implementation revision, and `independent_replay: {performed: true, reproduced_results: {...}, artifact_refs: [...]}` with your fresh hashed logs. Accept P00 first. Updating its reviewer changes the report file hash: update the P01/P02/P03 `input_artifacts` entries of `kind: phase_report` to that exact accepted P00 report hash and revision, then review those reports. Do not change their source revisions unless code changed; changed code needs renewed evidence.

Run the verifier separately for each phase:

```sh
python3 scripts/verify_game_plan.py \
  --manifest docs/game-lab/phase-manifest.json \
  --reports artifacts/game-verification/game-runtime-v1 --phase P00
```

Repeat with P01, P02 and P03. P00/P01/P03 should reach exit 0 after valid review and prerequisites. **P02 may show phase pass while still exiting 2** because the related `target_runtime_ready` milestone is blocked. Never forge a live read to make that command return zero. P04 can consume a static-only P02 record. Keep Windows/Android availability separate from macOS acceptance.

## Files repaired in this pass

- P00: `scripts/verify_game_plan.py`, `backend/tests/test_verify_game_plan.py`, `src/types/gameRuntimeValidator.mjs`, its `.test.mjs`, and six new `mapping_recipe__unknown_*` parity fixture/expectation files under `contracts/game_runtime/fixtures/parity/invalid/`.
- P01: `backend/bughunter/game_runtime/knowledge.py`, `knowledge_benchmark.py`, `backend/bughunter/library_context.py`, `backend/bughunter/game_lab.py`, `backend/tests/test_game_runtime_knowledge.py`.
- P02: `backend/bughunter/game_runtime/targets.py`, `scripts/qualify_game_target.py`, `backend/tests/test_game_runtime_targets.py`, and the installed-wrapper discovery in `backend/bughunter/op_adapters/mac_appstore_memory_esp.py`.
- P03: `backend/bughunter/memory_engine/{__main__,mac_adapter,windows_adapter,ownership,protocol,server}.py`, `backend/bughunter/__main__.py`, `backend/bughunter/mac_memory.py`, `backend/tests/test_memory_engine.py`, `backend/tests/test_mac_memory_lifecycle.py`, `src-tauri/src/game_bridge.rs`, `src-tauri/resources/mac_memory_mcp.py`, `tests/mac_mcp/test_ownership.py`.
- Evidence/docs: `docs/game-lab/phase-manifest.json`, this handoff, the README handoff link, and the new artifact runs/scripts/logs.

Existing `src/taskIntent.ts`, shared Python/Rust contract repairs, fixture/oracle repairs and `src-tauri/src/lib.rs` module exports were preserved. They are included where needed in source snapshots but were not newly rewritten in this repair pass. `backend/bughunter/chat.py` and `harness_actions.py` were not edited here.

`codex-P00-P03-repair-001/final/changed-files.json` is the complete change/hash inventory; `changes.patch` is the focused delta from the captured starting files. `rollback-verification.json` records a reverse-patch replay in a temporary tree. It does not mean the live workspace was rolled back. Files added to the edit scope after the initial snapshot use their clean HEAD version as the stated baseline.

## Interfaces P04 must reuse

Knowledge entrypoint: `KnowledgeService.prepare_plan(session_id, task_description, platform, operation_kind=None)`. It persists cited `consult_procedure` steps, rejected platform passages and unsupported assumptions. Those steps are planning evidence, not executable native operations.

`LibraryContext.game_task_context(query, session_id, project_ids=(), platform='unknown')` binds collections and creates the draft. GameLab stores a `game_knowledge_session` link so follow-ups retain context. Library project IDs and GameLab project IDs are distinct. Private course access is explicit through desktop `gamelab.bind_knowledge` with `project_id`, `knowledge_project_ids`, `target_platform`; the create-project API accepts the same binding fields. The actual imported course used in this replay belongs to library project `project_d2316cc58c11473ea01d5c4d`. Do not silently grant every game session all private projects or expose collection rebinding as a model tool.

Native helper entrypoints: `python -m bughunter.memory_engine` and `python -m bughunter --memory-engine`. The latter is the packaged-capable argument route, not evidence of a newly shipped bundle. Private methods currently implemented are bootstrap, attach, read, write, tool_control, detach and shutdown. Every read/write carries session and process identity; addresses stay strings. Structured partial failures must not be interpreted as successful values.

Rust: `BridgeState::attach`, `call_internal`, `set_notification_handler`, `detach`. Notification handlers run on the reader thread: enqueue and return, never block or call back into the bridge. The helper table is currently locked during a synchronous call. **P04 must change that before claiming responsive concurrent Stop.** The bounded Rust writer has no durable dispatch ledger; a send timeout may mean an operation was already queued. Treat uncertain writes as unknown, never retry them automatically.

Current bootstrap checks protocol readiness on a private pipe. It is not the P04 authenticated claim/completion service: durable executor epochs, per-operation credential validation and dispatch receipts do not exist yet. `BridgeState::public_proxy` rejects internal names in isolation; P04 must enforce that rule at the actual Tauri renderer RPC entrypoint too. The desktop app has not yet registered this bridge as the app-wide game executor.

## P04 code plan and expected outcomes

Follow the existing [implementation plan](agent-implementation-plan.md), especially its execution ownership/transport contract and P04 acceptance IDs. Use one macOS controller lane first. Split files among agents only after checking TeamService claims; one integrator owns shared wiring and manifest/report edits.

1. **Durable broker — `backend/bughunter/game_runtime/broker.py`, dedicated tests.** Add specialized SQL tables/migrations through `Store.connection(immediate=True)` for operations, executor registration/epochs, attempt receipts and feature/session state. Reuse `NativeOperation` and its state machine. Claims and completions must compare status, revision, epoch, attempt, session/stop generation and process identity atomically. Record dispatch intent before mutation. Identical receipts are idempotent; conflicting receipts produce a diagnostic event. Expected result: competing claims have one winner, stale completions cannot advance a new task, and a write with a lost reply becomes `outcome_unknown` without replay. This supplies `P04.races` and the durable part of `P04.crash`.

2. **Execution service and private bridge — `game_runtime/service.py`, `app.py`, `__main__.py`, `src-tauri/src/game_bridge.rs`, `lib.rs`.** Add public durable session/status/stop APIs and private claim/dispatched/complete/heartbeat handlers. Register a single app-wide executor keyed by machine/device and process birth. Bootstrap an in-memory credential; bind every private request to the registered helper identity, epoch and operation. Wire rejection of internal methods into the real renderer proxy. Keep credentials out of JSON logs, settings and model context. Refactor synchronous helper locking so a slow read cannot prevent control requests. Expected result: real backend→Rust→helper→external fixture receipts; forged renderer completion, missing credential and stale epoch rejected. Browser-only mode must report native execution unavailable.

3. **Chat integration — existing harness/operator registrations, `game_lab.py`, `library_context.py`, and the existing chat UI's small status/Stop surface.** Discover the actual current integration files before editing. Reuse the selected provider and existing plan/auto/full harness behavior. Carry target identity, knowledge plan ID and exact collection bindings into durable tasks. A request such as “remove kill cooldown for local Among Us play using my course” must stay a game task; a later “use that lesson” must stay attached to it. Expected result: `P04.chat_flow` proves real inspect/apply/readback on the external fixture and shows pending/blocked/verified evidence accurately. The unavailable real game should produce a clear limitation, not a fixture result presented as gameplay success.

4. **Stop and recovery — broker/service, native bridge and failure-injection tests.** Implement generation-bound Stop for feature/session/global scopes. Cancel queued work; request quiescence for dispatched work; allow only already-declared cleanup bound to the original recipe/process. Use the plan's 2-second quiescence, 2-second cleanup, then 2-second terminate/reap deadlines. A lease expiry starts recovery investigation, not takeover. Confirm old helper exit before a replacement writes. Preserve ownership when termination cannot be confirmed. Expected result: `P04.stop`, `P04.crash`, `P04.recovery` cover scan/write races, helper death, lost replies, cleanup timeout, changed target identity and shared readers interrupted by forced termination. Never restore an old countdown after time has elapsed.

5. **Integration acceptance and first human review.** Produce `P04/run-001/report.json` with accepted shared P01 and same-platform P03 report hashes. Add actual chat-to-native receipts, race logs, crash/restart evidence and before/after routing results. Investigate the existing 49 backend failures explicitly; any narrow quarantine needs its reason and baseline evidence, not a blanket ignore. Present the user with a runnable desktop flow: submit a fixture task, see cited evidence/progress, apply and verify a value, Stop it, then restart after a forced helper failure and see the honest recovery state. Expected result: all six P04 checks pass and the user can review the behavior. Do not label live Among Us cooldown, saved pointer recipes, ESP or health complete; those require later target/feature phases.

P05 remains the saved-pointer/scanning phase; P06 requires actual game behavior acceptance. Do not let P04 integration quietly grow into those phases or website redesign.

## Research inputs retained

The pinned Cpp2IL asset and release manifest are recorded locally with SHA-256. The original macOS ARM64 asset digest is `6670ddb93f28d4e7f329251d2597d2abbc6ba23e46382f7ca7b5e437ec93606a`; the CLI separately records its locally signed copy. Its release is [Cpp2IL 2022.1.0-pre-release.21](https://github.com/SamboyCoding/Cpp2IL/releases/tag/2022.1.0-pre-release.21).

Native behavior references used during implementation: [LLDB SBProcess](https://lldb.llvm.org/python_api/lldb.SBProcess.html), [LLDB memory-region lists](https://lldb.llvm.org/python_api/lldb.SBMemoryRegionInfoList.html), and [Windows GetExitCodeProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess). Measured test results, rather than documentation alone, establish the macOS fixture behavior described above.
