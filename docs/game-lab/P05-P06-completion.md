# P05/P06 app integration — completion and review handoff

2026-11-16. Implementation authors: Codex, GPT-6.1 Sol discovery and mapping subagents. Parent Codex reviewed, integrated and tested the combined result. Independent phase acceptance remains pending.

This supersedes the integration-gap sections of `P05-P06-codex-review.md`, `P05-P06-handoff.md`, and `P06-live-launch-instructions.md`. Historical source reports and their reviewer records were preserved.

## Built

Game chat now exposes working native discovery, saved mappings and repeated feature controls. Operations travel through the existing task-scoped service, durable broker, private Rust executor and serialized memory helper. No detached verifier is used for chat execution.

- Scans support exact/range/unknown-initial/byte patterns, changed/unchanged/increased/decreased/exact refinements, bounded pages and explicit close. The helper isolates each chat's scan IDs. Coverage, skipped reads, time/memory/result limits and cancellation are visible in receipts. A task stop releases its snapshots while shared readers can continue.
- Pointer searches produce measured module-relative or explicitly process-bound candidate paths. A shorter absolute chain survives a deeper unsuccessful search. Module extents are measured from loaded sections. Among Us diagnostic candidates require measurement of the running main executable; the composite session fingerprint is not confused with the main-image hash. Discovery never upgrades a candidate to a confirmed player or writes to test its identity.
- Mapping resolution walks the stored recipe in the native process. Resolved values are never used as memory addresses. Observation requires a successful resolver receipt for the same task, recipe revision, process birth and fingerprint. The revision check remains valid under concurrent edits. The observed object path is retained and compared before the first feature write.
- Mapping import/export is available from chat, validates whole transfers atomically, retains provenance and regenerates destination IDs. Imports clear live observations and restart verification. Absolute roots become stale. Limits are 1,000 recipes / 8 MiB JSON. Legacy migration uses local provenance markers; old migrations lacking such a marker can duplicate once instead of trusting arbitrary cited evidence as a completed migration.
- Checkpoint export/restore persists actual candidate values and unknown-initial snapshots, validates hashes and the identical process birth/build, and can resume refinement. It is a `ScanSession` API, not a chat command or a promise to continue an interrupted address sweep. Incomplete sweep coverage stays incomplete after restoration.
- Features resolve from saved observed recipes, pin their initial object path, use conditional writes/readback, and schedule repeated ticks as durable operations. Feature Stop disables new ticks before cleanup; task Stop, global Stop, helper death and app close use the same ownership chain. No timer thread survives independently of the executor. Runtime guard recipes can check local mode, role, alive status and round identity before a live write; all four measured guards are required for a real-game start.
- Changes or invalidation of a feature/guard mapping halt scheduling. Duplicate starts are refused. A cleaned-up feature can start a new lifecycle with a new ID/generation; an obsolete lifecycle's Stop cannot stop the new one. An unknown write is never replayed. Failed ticks initiate Stop/cleanup rather than merely hiding the scheduler. Countdown Stop never restores an elapsed captured value.
- Persistent LLDB wraps one guarded update in a short transaction so the target cannot change its timer or guard between the observations and conditional write. The target resumes even when a guard rejects the update. A target already paused before attachment is refused without changing its state. Mach access still uses conditional comparison and can reject a concurrent update rather than pretending success.
- Feature intervals are anchored to successful receipts, including a slow operation: no subsequent tick is queued until 500 ms after its receipt. A deterministic clock regression covers both the initial start and a delayed tick.
- Running games launched from a macOS relocated bundle are accepted only after every qualified bundle-relative input (main executable, UnityFramework, metadata and Unity-version data) matches its recorded hash and OS birth remains unchanged. The real running Among Us bundle matched all four (`running-bundle-qualification.json`); native attachment is still denied.
- The private completion endpoint atomically handles Stop advancing the revision of the same already-dispatched attempt. Executor, attempt and process bindings remain mandatory. This accepts the observed receipt without rerunning native execution.
- The activity panel displays scan coverage/results, pagination, mappings, feature update status and a Stop button per feature. It preserves existing task/session controls. The model's tool descriptions and capabilities now reflect the implementation. Generic feature support does not claim Among Us gameplay qualification.
- A fixture identity bug uncovered by the real native feature test was fixed: `start_time_ns` is the OS process birth, rather than a newly sampled monotonic timestamp. This agrees with the helper, process lease, mapping binding and restart verifier. Desktop RPC teardown now cancels queued handlers and drains in-flight handlers before closing shared services.

## Verification observed

Artifact root: `artifacts/game-verification/game-runtime-v1/codex-P05-P06-completion-001/`.

- `final-integration-reviewed.log`: 123 passing Python tests, covering new integrations, scans, transfer/migration, feature lifecycle, native discovery and prior review regressions.
- `regression.log`: 264 passing Python tests across contracts, fixture, broker, service, native transport, knowledge, target qualification, phase verifier, baseline machinery, harness and legacy memory lifecycle. These test selections do not overlap the 123 above.
- `external-feature-replay.log`: 31 passing integrated/fixture checks after correcting OS birth identity. These overlap the counts above. The final integration suite also includes the corrected real native feature test.
- `rust-final.log`: 48 Rust tests pass, zero ignored. The production chat-to-native fixture test now scans, saves/resolves/observes a mapping, starts/monitors/stops a feature, confirms cleanup, and exercises the original session/app-close/shared-reader cleanup paths.
- `ui-replay.log`: the game activity UI test passes, including incomplete scan coverage, paged results, feature Stop, session Stop and retained uncertain-write evidence.
- `build-final.log`: `npm run build` succeeds. Vite reports the existing large-bundle advisory.
- `desktop-build.log`: `cargo build --manifest-path src-tauri/Cargo.toml` succeeds. Existing warnings in `execution_control.rs` / `mac_desktop.rs` remain.
- `service-final.log`: 69 focused service/broker/transfer tests pass, with additional review races represented in the final suite.
- `desktop-launch.log`: native development app launched successfully; OS process list observed PID 21737 running `target/debug/bughunter-claudex`. Its GUI/window observation is recorded separately and is not inferred from the process being alive.

`independent-audit/` contains a fresh GPT-6.1 Sol review. Its first pass found the scheduler interval and relocated-bundle issues above; follow-up review/replay records the final verdict after repair. This code audit does not accept historical phase reports or live gameplay.

The model decisions in the automated Rust chat test are scripted; production tool routing, SQLite, executor, helper, process memory and cleanup are real. No paid model turn or actual human gameplay acceptance is claimed. A full unrelated backend-suite comparison against the historical 49 failures was not rerun.

## Real Among Us check

Desktop Accessibility and Screen Recording permissions were reported granted. `agent-desktop launch AmongUs` started PID 11282. A later activate call observed its visible, focused window `w-15582`, titled `Among Us` (`game-launch-replay.json`). These observations establish launch and desktop visibility; they do not establish a successful gameplay action.

`live-readonly/summary.json` records the measured executable, process birth and exact SHA-256:

`3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992`

LLDB attachment was denied by macOS (`Not allowed to attach to process`). The verifier returned blocked, exit 2. No Among Us memory write occurred. No game signature, entitlement, installation or OS protection configuration was changed.

A usable measured LocalPlayer root and game-specific guard mappings still require live access/qualification. The static `LocalPlayer +0x0` / `killTimer +0xB0` field offsets alone do not establish them. Do not insert guessed roots or arbitrary heap addresses disguised as module RVAs. Actual earlier accepted kills, normal behavior after disable, round/death/disconnect handling and three fresh Among Us launches remain unverified. Fixture behavior is recorded separately.

## Available tools and example flow

Existing `game_targets`, `game_session`, `game_fixture`, `game_read`, `game_write`, `game_status`, `game_stop` remain. New complete chat tools:

- `game_scan`, `game_scan_refine`, `game_scan_results`, `game_scan_close`
- `game_pointer_search`, `game_candidate_search`
- `game_mapping_save`, `game_mapping_list`, `game_mapping_revalidate`, `game_mapping_observe`, `game_mapping_export`, `game_mapping_import`
- `game_feature_start`, `game_feature_monitor`, `game_feature_stop`

For a disposable test requested by the user: start `game_fixture`, scan its countdown for 60, save `mod:region_0+0x0` with one add step `0x20`/width 4/type `u32`, revalidate and wait for the resolver operation, observe that receipt, start the feature with a declared static disable value, then Stop it. These exact fixture offsets belong only to the layout produced in this repository. Real-game requests must use measured game recipes instead.

The agent should poll durable operation IDs before claiming a result. Further pages use the returned `scan_id` and `next_cursor`. A normal feature tick is scheduled no sooner than 500 ms after the preceding successful receipt, and can be slower under debugger load. No 30 Hz gameplay performance claim is made.

The operator wants supervision with minimal manual steps. Discover PIDs automatically; use computer-control tools and foreground input for UI actions when granted. Ask the operator only for a real OS prompt or inaccessible action. Human acceptance checkboxes remain the operator's observations.

## Review and next work

P05 and P06 implementation status is `ready_for_review` for the macOS code lane; this does not mark either phase verified. The phase source paths include the new helper and regression files. P00, P03 and P04 also need refreshed source-bound reports because shared code changed. Historical reports and reviewer identities remain untouched. A reviewer who did not author the final implementation must accept a fresh revision. The current phase verifier is expected to reject old report hashes against changed sources.

Further game-specific work depends on qualifying native access and measuring the missing LocalPlayer/guard mappings. Windows, P07 overlay, Android feasibility, packaging/release and future update qualification retain their original plan gates; they are not claimed by this P05/P06 integration delivery.

All current changed files, before/after hashes, a unified patch and a forward/reverse check in a disposable tree are recorded beside the completion report. The workspace was not rolled back, and no commit was created.

## Reproduce

From `backend/`:

```sh
uv run pytest -q --timeout=60 tests/test_game_runtime_integrated.py tests/test_game_runtime_chat_integration.py tests/test_game_runtime_discovery_native.py tests/test_game_mapping_transfer.py tests/test_game_runtime_scans_pointers.py tests/test_game_runtime_features.py tests/test_game_runtime_review_regressions.py
uv run pytest -q --timeout=60 tests/test_game_runtime_contracts.py tests/test_game_runtime_fixture.py tests/test_game_runtime_broker.py tests/test_game_runtime_service.py tests/test_game_runtime_native.py tests/test_game_runtime_knowledge.py tests/test_game_runtime_targets.py tests/test_verify_game_plan.py tests/test_baseline_report.py tests/test_memory_engine.py tests/test_harness.py tests/test_harness_modes.py tests/test_mac_memory_lifecycle.py
```

From the repository root:

```sh
cargo test --manifest-path src-tauri/Cargo.toml --lib -- --test-threads=1
npx playwright test tests/ui/game-runtime.spec.ts
npm run build
cargo build --manifest-path src-tauri/Cargo.toml
```

The local test environment has pytest-timeout installed. Development launch is `npm run desktop:dev -- --no-watch`.
