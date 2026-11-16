# P04 — Chat and durable native execution

P04 now connects a game conversation to persisted operations and a private desktop executor. The macOS development path has been exercised against separate, real fixture processes. This is the first human-review checkpoint. It is **ready for review**, not independently accepted, and does not establish live Among Us cooldown, pointer discovery, ESP, or health support.

The implementation used GPT‑6.1 Sol agents for the broker, native executor groundwork, and independent acceptance of the frozen P00–P03 prerequisites. Their usage limit stopped further delegation. The parent completed integration, repairs, fault tests, and replay. The parent is the final implementation author and must not be recorded as its independent reviewer.

## Start here, Claude

The user wants to chat with an AI that can use their saved game course, inspect game memory, find and reuse pointers, control tools, and eventually implement requested game features. They explicitly asked to focus on games, not improve the website. They prefer simple language and asked for GPT‑6.1 Sol implementation agents with parent verification. The immediate task is to finish P04 review; do not infer that the whole game roadmap is complete.

The user's latest concern was: **“How much P4 is built? Won't it need human intervention? What is its test? I do not see anything on my screen.”** Explain that P04 implements the Chat-to-native execution path, its automated tests ran in background processes, and real-model human review remains outstanding. Auto mode can perform the ordinary test steps after the task's existing access setup; the user still chooses the task/model, handles any actual OS access prompt, and can Stop. Do not promise perfect course learning or that every game operation will run without input.

The current desktop development process was launched with `npm run desktop:dev -- --no-watch`, and PID **64331** was still running when this handoff was finalized. Its log is `artifacts/game-verification/game-runtime-v1/P04/run-001/desktop-launch.log`. Recheck the PID before using it; do not launch another primary backend against the same data directory. The default app data directory is `~/Library/Application Support/BugHunterClaudex`. No settings, model choice, or saved course content was replaced to run the automated tests; those used isolated stores. The unrelated Python `lsp_server.py` process is the editor helper, not the app.

The **Game task** activity appears only after starting a game conversation. An empty Chat screen does not show P04 controls. Launching the app did not submit a prompt or call the user's real model. Help the user perform the five review steps below and record what they actually observe.

Prerequisite context: P00 defines shared contracts and the verification/fixture harness; P01 retrieves and cites scoped course knowledge; P02 identifies the exact installed game/build and qualifies access; P03 supplies the native helper, transport and process ownership. P04 connects those pieces to durable Chat tasks, receipts, Stop and recovery. P05 discovery/pointer storage and P06 verified game behavior are still ahead.

The repository is dirty and **no commit was made**. `changed-files.json` gives the exact P04 file list; other dirty files include earlier P00–P03 work. Artifacts are gitignored, so preserve or explicitly package the evidence directory when moving to another checkout. Never reset the whole tree. The active `codex-selection-feedback` task owns mapping-chat UI/tests and its documentation; avoid those files or coordinate through the existing TeamService.

## Review the product

From the repository root, use `npm run desktop:dev`. The existing backend environment must be installed. A browser-only Vite tab cannot execute native operations. This review is for the macOS development build; packaged distribution and Windows execution have not been qualified here.

1. Choose your normal model and Auto mode. Send: **“Test the game runtime using a disposable fixture. Use my saved course where it applies. Read the test countdown, set it to 7, read it back, and restore its original value when I press Stop.”** Allow the task's App access if the existing task setup asks for it.
2. Confirm the conversation stays in the game task. The Game task activity should show desktop tools connected, a separate disposable target, a saved course-plan ID, and operation results. A plan ID can exist with no applicable passages; it is not proof that every course lesson was understood.
3. If the model has only proposed steps, use **Start disposable test target**, **Read test value**, and **Set test value to 7**. The first read should report 60, the write should report a verified readback, and a subsequent read should report 7. The button-created fixture write includes restoration on Stop. The model can request the same behavior with `restore_on_stop=true`.
4. Use **Stop game session**. Its declared restoration should report success and the session should become stopped. Follow up with a course reference before stopping the whole task: it should stay in the same conversation and game task.
5. Restart the desktop app and inspect the saved task. Completed receipts must remain visible. An interrupted write must never become a fresh automatic write.

The automated Rust test uses a scripted provider to make tool choices reproducible and avoid paid model calls. It runs the production harness, cited knowledge retrieval, typed tools, durable broker, Rust executor, helper, and OS memory access. Human review still needs to check the chosen real model's behavior.

## What was implemented

- `backend/bughunter/game_runtime/broker.py`: SQLite sessions, feature ownership, executor epochs and leases, atomic claims, dispatch intent, completion receipts, duplicate/conflict handling, and Stop generations. Writes with lost replies remain `outcome_unknown`. Mutation and its declared fixture restoration are committed in one transaction.
- `backend/bughunter/game_runtime/service.py`: public task-scoped operations, measured target/process identity, scoped course plans, fixture lifecycle, private credential checks, heartbeat/recovery, cleanup eligibility, and actual OS helper-death checks. Temporary MCP/RPC client shutdown does not stop the desktop's sessions.
- `backend/bughunter/app.py`, `__main__.py`, `harness.py`, `harness_modes.py`, `operator_tools.py`: reuse the existing provider/operator path, expose seven typed game tools only in game tasks, preserve Plan/Auto/Full behavior, and connect task/global Stop. The private credential is consumed at startup and is absent from model tools, public renderer requests, and HTTP execution.
- `src-tauri/src/game_executor.rs`, `game_bridge.rs`, `lib.rs`: one executor per target process, serialized ordinary work with responsive control frames, durable dispatch before native work, stale-receipt checks, bounded quiescence/cleanup/reaping, shared-reader preservation, private bootstrap, and app-close cleanup. Shutdown commits Stop before waking cleanup workers. Forced helper termination includes its Unix process group; ownership remains if exit cannot be confirmed.
- `backend/bughunter/memory_engine/__main__.py`, `server.py`, `mac_adapter.py`: responsive helper control, serialized memory work, process-bound expected-value writes, and a single LLDB attachment for compare/write/readback. LLDB cannot consume helper stdin. Cleanup has a two-second budget rather than three sequential debugger attachments.
- `src/GameRuntimeActivity.tsx`, `src/Chat.tsx`, `src/taskIntent.ts`: game status, actual operation receipts, fixture controls and Stop in Chat; explicit game/course requests and their follow-ups keep their game task. A pending restoration does not disable readback, and a second restoration-backed write requires stopping the existing session first.
- Tests added: `backend/tests/test_game_runtime_broker.py`, `test_game_runtime_service.py`, `test_game_runtime_native.py`, `tests/ui/game-runtime.spec.ts`. Rust bridge/executor tests and `tests/unit/task-intent.test.mjs` were extended.

Normal feature Stop preserves other features and readers. Session Stop preserves another session on the same target. Global Stop revokes all sessions. A helper that cannot quiesce is terminated; every subscriber then records interruption. The default control poll is 100 ms, the lease is five seconds, and the Stop budgets are two seconds each for quiescence, cleanup, and terminate/reap. Restoration applies only to the static disposable fixture; a game countdown is not restored to an old value after elapsed time.

## Reproduce and inspect evidence

The final command records, exact exit codes, log hashes, source inventory, changes patch, and rollback replay are under `artifacts/game-verification/game-runtime-v1/P04/run-001/`. Earlier failed iterations remain under `P04/baseline/` and explicitly named historical logs; they are not final acceptance evidence.

Run from the repository root. Replays write new timestamped directories under `run-001/replays/`, preserving the frozen logs. An optional second argument selects a separate reviewer output directory:

```sh
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py p04
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py prerequisites
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py knowledge_integration
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py rust
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py routing_backend
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py routing
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py typescript
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py ui
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py legacy
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py build
python3 artifacts/game-verification/game-runtime-v1/P04/run-001/replay.py full_backend
```

The native crash test deliberately exits after an actual write and before its response. An independent memory oracle sees 7, while the ledger preserves the uncertain original attempt. Other tests cover competing claims, forged/stale receipts, queued and dispatched Stop races, blocked native work, cleanup timeout, changed process/build identity, helper-death-before-takeover, shared readers, and app-close restoration. Scan discovery itself belongs to P05; the blocking cancellation test injects a long-running native job into the same helper worker.

`logs/full_backend.log` remains a failing whole-repository run. `baseline-comparison.json` lists every existing failure by exact test ID and compares it with the pre-P04 log. No tests were blanket-skipped or changed to hide those failures. Most concern existing website request-policy/approval expectations; the remainder include old mock-interface, asset, workflow, and checkout-fixture failures. P04 routing evidence consists of the targeted passing checks plus this explicit unchanged-failure comparison. The independent reviewer must inspect those exceptions; this is not a claim that the full repository suite is green.

## Observed final results

- P04 broker/service/native fault checks: **26 passed**.
- P00–P03 focused regression checks: **215 passed**, plus **9 library/setup integration checks**.
- Rust: **47 passed**, including the real chat-to-native sequence, shared-reader preservation and app-close restoration.
- Harness/provider: **17 passed**; request routing: **46 passed**; browser UI: **15 passed**.
- TypeScript checking and `npm run build`: **exit 0**.
- Legacy macOS bridge: **21 passed, 4 skipped**. Those unavailable legacy live tests are not used to satisfy P04's native checks.
- Whole backend: **1,262 passed, 49 failed, 1 skipped**. All 49 failing IDs match the pre-P04 baseline; zero new failures.

## Current acceptance boundary

The independent prerequisite agent accepted the frozen P00/P01/P03 shared/macOS evidence before P04 integration and reproduced P02's static qualification. P04 changed P01 routing and P03 transport/native files. Therefore P01 and P03 received **new run-003 regression reports** against current source. During final handoff, Claude independently accepted those exact source revisions; the prerequisite verifier now exits 0 for P01 and P03. P04 references their updated report hashes. Their old accepted run-002 reports remain historical evidence. P00 and P02 source inputs remain unchanged.

P04's `report.json` deliberately leaves `reviewer`, `reviewed_revision`, and `independent_replay` empty. The manifest's `status_policy.reviewer_must_differ_from_author` and `changed_inputs_invalidate_affected_reports` require this. Verification is expected to exit 2 until a different reviewer accepts P04. The renewed P01/P03 prerequisites now pass. Do not fill those fields merely to make the command green.

Current P04 source revision: `sha256:091fccb3fb6eac176d37f174abc431da3f392c978670fcbeea2c415b4a76fbbe`. Inspect `report.json` and recompute source hashes before accepting it. `prepare_review.py` is the author's historical bundle generator; it now refuses to overwrite independently accepted prerequisite reports. **Use `replay.py` for review**, save fresh logs in a new reviewer directory, and do not rerun the author generator over accepted evidence. A reviewer who changes implementation code must arrange a different reviewer for the changed revision.

```sh
python3 scripts/verify_game_plan.py \
  --manifest docs/game-lab/phase-manifest.json \
  --reports artifacts/game-verification/game-runtime-v1 --phase P04
```

Real installed-game memory access remains unverified. P02's prior Among Us runtime probe was blocked with zero bytes read. Windows and Android runtime acceptance, a signed packaged launch, a real-model human session, P05 scan/pointer storage, and P06 gameplay behavior are still separate work.

## Message for Claude / the next reviewer

> P04 is implemented; start with `docs/game-lab/P04-review-and-handoff.md` and `P04/run-001/report.json`. Preserve the dirty workspace and unrelated selection-feedback work. Independently replay the current source, especially the real Rust chat-to-native test, lost-write-reply test, shared-reader Stop, app-close restoration, and temporary-client shutdown regression. Review the exact baseline exceptions rather than skipping the whole backend suite. P01/run-003 and P03/run-003 have now been independently accepted by Claude; preserve their reviewer records and check their source hashes before consuming them. Record your actual identity, source digest and separately hashed P04 replay logs, then independently accept P04 and collect the user's first human review. Do not claim Windows or live Among Us support from the macOS fixtures. After P04 acceptance, P05 is the next implementation phase: value discovery, pointer recipes, persistence, restart validation and storage budgets, using this broker/native path rather than adding another agent loop.
