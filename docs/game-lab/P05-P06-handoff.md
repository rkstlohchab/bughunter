# P05 + P06 — code gates shipped, live gates pending

Date: 2026-11-15. Author: Claude Opus 4.7.

## P05 — scan engine + pointer discovery + mappings + migration

Report: [P05/run-001/report.json](../../artifacts/game-verification/game-runtime-v1/P05/run-001/report.json)
Implementation revision: `sha256:78632e4b14a3a68703788c52177e2377e787bb7ca4bb0ad80b308561dbaf383d`.
Tests: **19 passed** (`backend/tests/test_game_runtime_scans_pointers.py`).

Files shipped:

| file | role |
|---|---|
| [backend/bughunter/game_runtime/mappings.py](../../backend/bughunter/game_runtime/mappings.py) | MappingStore + immutable revisions; candidate/observed/restart_verified/stale machine; revise() always resets to candidate. |
| [backend/bughunter/game_runtime/scans.py](../../backend/bughunter/game_runtime/scans.py) | ScanSession with exact/range/unknown-initial + changed/unchanged/increased/decreased/exact refinements. 4 MiB chunks, 256 MiB resident, 2 GiB snapshot budget enforced. Cancellation + partial-read reporting. |
| [backend/bughunter/game_runtime/pointers.py](../../backend/bughunter/game_runtime/pointers.py) | resolve_recipe (module-relative + absolute + +0x0 roots) + discover_pointer_paths (Cheat-Engine reverse walk, default depth 6 / max 12, window ±4096). revalidate_identity refuses cross-target reuse. |
| [backend/bughunter/game_runtime/migrations.py](../../backend/bughunter/game_runtime/migrations.py) | SQLite backup API; idempotent `migrate_legacy_mappings`; `rollback()`. Absolute-root recipes immediately marked stale on migration. Never promotes status. |
| [scripts/verify_game_profile_restarts.py](../../scripts/verify_game_profile_restarts.py) | CLI: resolve a recipe across ≥3 fresh processes; distinct (pid, start_time_ns) + matching value required to promote to `restart_verified`. |

**Status:** `ready_for_review`. External reviewer different from
owner `claude-opus-4.7-P05-impl-2026-11-15` must accept.

## P06 — Among Us local no-cooldown

Report: [P06/run-001/report.json](../../artifacts/game-verification/game-runtime-v1/P06/run-001/report.json)
Implementation revision: `sha256:dbb9b1363ef7efbfd698ab6819e7dc1204479716a995af7929510b739ce2e758`.
Tests: **10 passed** (`backend/tests/test_game_runtime_features.py`).

Files shipped:

| file | role |
|---|---|
| [backend/bughunter/game_runtime/features.py](../../backend/bughunter/game_runtime/features.py) | FeatureRun lifecycle: requested→starting→running→verifying→monitoring→stopping→stopped (+ failed/blocked side transitions). apply() writes via compare_write_value + readback verify. stop() never restores a stale captured countdown. |
| [backend/bughunter/game_runtime/among_us.py](../../backend/bughunter/game_runtime/among_us.py) | Among Us specific recipe: PlayerControl.LocalPlayer static offset 0x0, killTimer instance offset 0xB0 (f32). scan_for_local_player_candidates brute-forces the pointer. |
| [scripts/verify_game_feature.py](../../scripts/verify_game_feature.py) | CLI: run baseline / apply / stop phases, write observations.jsonl + summary.json + a gameplay-observations.md template for operator attestation. |

### P06 live gate — what's blocked and what unblocks each check

The P06 code gate runs; the plan explicitly requires live gameplay for
`P06.baseline`, `P06.readback`, `P06.behavior`, `P06.disable`, and
`P06.restarts`. Automated checks alone cannot reach those; the
`gameplay-observations.md` template is where operator attestation
lands.

**What the operator does:**

1. Launch Among Us in **Freeplay** or a **local Impostor-capable
   lobby**. Report the PID (shown in Activity Monitor or `pgrep -x AmongUs`).
2. Run:
   ```sh
   cd /Users/raksithlochabb/Downloads/bughunter
   uv run python backend/bughunter/game_runtime/among_us.py 2>/dev/null  # optional sanity
   python3 scripts/verify_game_feature.py \
     --profile <recipe-id-from-mapping-store> \
     --feature no_cooldown \
     --pid <AmongUs-pid> \
     --duration-s 20 \
     --apply-value 0.0 \
     --report-dir artifacts/game-verification/game-runtime-v1/P06/live-01
   ```
3. Fill `artifacts/game-verification/game-runtime-v1/P06/live-01/gameplay-observations.md`
   with YES/NO against each live checkpoint.
4. Quit, relaunch Among Us twice more. Run
   ```sh
   python3 scripts/verify_game_profile_restarts.py \
     --profile <recipe-id> \
     --restarts 3 \
     --launcher 'open -W /Applications/AmongUs.app' \
     --build-fingerprint 3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992 \
     --report-dir artifacts/game-verification/game-runtime-v1/P06/live-restarts
   ```
   (The `--launcher` form only works if the game can be headless-spawned to a
   known PID. Freeplay typically can't — in that case run the restart
   verification by hand, launching Among Us three times and running
   `verify_game_feature.py` against each new PID.)

### What's preserved for the reviewer

- Among Us install fingerprint (`sha256_main`)
  `3b2f71e42a51fdb16449dfacec45a19be78ba29b95c43222aa6de46ed6a5f992` —
  matches P02's static-replay evidence exactly.
- Kill-cooldown offsets (`PlayerControl.LocalPlayer +0x0`, `killTimer +0xB0`)
  cited against
  [`PlayerControl.cs:649`](../../artifacts/game-verification/game-runtime-v1/claude-P02-static-replay-001/static-analysis/derived/DiffableCs/Assembly-CSharp/PlayerControl.cs#L649)
  and
  [`PlayerControl.cs:684`](../../artifacts/game-verification/game-runtime-v1/claude-P02-static-replay-001/static-analysis/derived/DiffableCs/Assembly-CSharp/PlayerControl.cs#L684).

### What's NOT claimed

- No live Among Us memory read occurred. `target_memory_access` for
  P02 remains `blocked` (needs an active Among Us process).
- The `placeholder` root in the shipped recipe needs the operator to
  bind the real `PlayerControl` static_fields RVA before P06 live
  checks promote beyond `blocked`.
- Windows support, packaged release, and the non-root Android phone
  work are out of scope for this pass.
