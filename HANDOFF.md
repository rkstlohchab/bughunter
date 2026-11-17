# Handoff: engine-aware memory mapping and live analysis

Updated 2026-11-17. Plan: `~/.claude/plans/just-draft-a-plan-delightful-wave.md`.

## Request

Improve the app's game runtime for the user's build: Unity and Unreal memory-mapping techniques, realtime memory analysis, and durable findings, surfaced through the chat agent. Everything added is read-only against the target.

## What exists now

- `backend/bughunter/game_runtime/engine_map/`: engine detection (`detect.py`), IL2CPP metadata reader (`il2cpp_metadata.py`), IL2CPP runtime probes (`il2cpp_runtime.py`: class, instances, field), Unreal probes (`unreal_runtime.py`: name pool, object layout and object array), and resumable bounded sweeps (`memview.py`, `probe.py`).
- `backend/bughunter/game_runtime/live_analysis.py`: pure slot classification (timer with measured rate, counter, flag, pointer, vector3 candidate), dissect and diff.
- `backend/bughunter/memory_engine/analysis.py`: helper lane for `analysis_action` requests (engine_detect, probe, probe_close, watch, dissect, snapshot, diff). Requests arrive as broker `read`/`read_only` operations, so there is no Rust or contract change. Each action has a deadline of at most 15 s, a byte budget and a cancel event in the discovery registry; state is released with the task.
- `backend/bughunter/game_runtime/findings.py`: findings are created only from succeeded read receipts. A second operation reproducing a finding moves it from `candidate` to `observed`; `abs:` findings are scoped to one process.
- `service.py`: `engine_detect`, `metadata_search`, `engine_probe`, `object_dissect`, `watch` (+ repeat pump), `watch_stop`, `snapshot`/`snapshot_diff`, `findings`, `finding_promote`; findings and watches appear in `status()`; agent recipe text added to `context()`.
- Agent tools in `operator_tools.py`: `game_engine_detect`, `game_metadata_search`, `game_engine_probe`, `game_object_dissect`, `game_watch`, `game_watch_stop`, `game_snapshot`, `game_findings`, `game_finding_promote`. All except promote are in `READ_TOOLS` and so usable in Plan mode.
- Chat panel `src/GameRuntimeActivity.tsx`: an engine tag, a Findings fold, and watch/probe/diff facts on operations.

## Measured facts (this machine)

- Combat Master: `discover_installed()` already qualifies it (arm64 slice, UnityPlayer.dylib + GameAssembly.dylib, metadata hashed); no `targets.py` change was needed.
- `global-metadata.dat` version 39: header of 31 int32 (offset, size, count) triples from byte 8 to 380, tiling all 17,519,268 bytes. Identifier section = section 2 (offset 912468, size 3014764, 155,691 identifier-like names, e.g. `_chargedAmmoLeft`, `ActiveWeaponTotalAmmoLeft`, `IsNoAmmo`). Parse time is 0.09 s.
- Real attach path on a disposable target: backend `lldb`; watch achieved about 5.2 samples/s for one 64-byte window and 5.25/s for two addresses read as one covering span (3.6/s before that change, using a transaction).

## Builds and tests (2026-11-17)

- `cd backend && .venv/bin/python -m pytest tests -q`: 1522 passed, 2 skipped, exit 0. New files: `test_game_runtime_engine_map.py` (12), `test_game_runtime_live_analysis.py` (10), `test_game_runtime_findings.py` (8), `test_game_runtime_analysis_native.py` (1, uses the real lldb attach against `tests/_engine_target.py`).
- `npx tsc -p tsconfig.json --noEmit`: exit 0. `npx playwright test tests/ui/game-runtime.spec.ts`: 1 passed (extended with findings and analysis facts).
- `cargo test --manifest-path src-tauri/Cargo.toml durable_backend_executor_fixture_read_write_cleanup`: passed when run alone (exit 0). It failed once while the full Python suite was running in parallel; the helper was interrupted as `helper_exited`, consistent with a missed 5 s executor lease under load. Run it on its own.

## Not verified / gated

- Not verified in-game: Combat Master was not running during this session.
- Unreal probes have only been verified against synthetic images; no Unreal build is installed.
- The IL2CPP type-definition layout for metadata v39 is not sourced, so names are not mapped statically to their owning types; the live class probe does that instead. Cpp2IL compatibility with v39 was not tried.
- The Unreal index block shift cannot be determined from names in block 0 only; the probe reports it as `null` with its candidates.

## Next verifiable step

Start Combat Master in offline mode, then in a game-mode chat: `game_targets` → `game_session` → `game_engine_detect` → `game_metadata_search query=ammo` → `game_engine_probe probe=il2cpp_class name=<owning class>` (continue with `job_id` until complete) → `il2cpp_instances` → `game_object_dissect` with `il2cpp_name_slot` → `game_watch` on the instance while firing and reloading. Store evidence under `artifacts/game-verification/game-runtime-v1/combat-master-live-001/`. Over lldb, a full sweep of a large process may need several probe calls; the receipt shows covered and total bytes.

# Website testing: TBHM + ChatGPT branch (2026-11-17)

- `origin/feat/pentagi-haddix-commerce` is merged into the working tree with `--no-commit` (MERGE_HEAD present, nothing committed). Only `backend/bughunter/policy.py` conflicted; our version was kept because the branch's change is a subset of ours (both remove the autonomy short-circuit; ours also moves auto-approval into `PolicyService.queue`, failing closed). Commit or `git merge --abort` when reviewed. The game-runtime work above is still uncommitted in the same tree.
- `backend/bughunter/tbhm.py`: 33 TBHM sections mapped to pages of the stored "Bug Hunters Methodology Live" Day One/Day Two decks, with cited slide and transcript retrieval, per-project coverage (`tbhm_progress` entity), website-intent context for chat tasks, and `tbhm_methodology`/`tbhm_progress` tools for both the chat agent and the RunManager investigation agent. The branch's `checklist()` and PentAGI plan now link to these sections.
- Measured on a scratch copy of the production DB: all four TBHM sources detected by title; sections return page-cited slides and timestamped talk excerpts; context for a website request is about 7.4k characters.
- Tests: backend full suite 1543 passed, 2 skipped (before the two harness tool-set expectations were updated; both pass now). Playwright: 121/124; the 3 `mapping-research.spec.ts` failures reproduce on clean HEAD in a separate worktree, so they predate this work. The PentAGI panel's TBHM coverage block is type-checked only, not exercised in a browser.

# Live-site protection: route review + attack monitoring (2026-11-17)

- `backend/bughunter/route_review/`: route discovery (Express/Fastify/Hono, Next.js pages/app/server actions, SvelteKit, Remix, Flask/FastAPI, Django, Rails, Laravel, Spring, NestJS, Go, ASP.NET) and per-route flags with file:line, fix, TBHM section and live check. Source: local folder or https GitHub/GitLab/Bitbucket URL (shallow clone into app storage; private repos use the user's git credentials). Real check: expressjs/express cloned and reviewed, 55 routes in 1.7 s.
- `backend/bughunter/site_monitor/`: auto-detected nginx/Apache combined, AWS ALB, W3C (CloudFront/IIS/Azure), and JSON (Vercel/Netlify drains, Cloudflare HTTP/firewall, GCP, Azure Front Door); incremental ingest with rotation and gzip; signature + per-client behaviour detection; incidents with severity, possibly-successful hits, fix and nginx/Cloudflare block rules; optional background watch per source.
- Not built: an HTTP log-drain receiver (hosts that push logs need a public endpoint) and cloud log APIs; today logs must be on disk (synced, exported or mounted). Monitoring never changes the server.
- Tests: backend full suite 1566 passed, 2 skipped. New: test_site_monitor.py (18), test_route_review.py (3). Not verified against the user's real site, repo or logs.
