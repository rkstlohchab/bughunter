P00 run-002 independent review — repairs required

Reviewer: Codex (`codex-p00-replay-002`). Date: 2026-11-14.

P00 is **not accepted**. The recorded test commands pass, but additional
reproductions expose contract-validation and milestone-validation failures.
Two source hashes also differ from the submitted snapshot. The phase manifest
remains unchanged at `ready_for_review`; P01 must wait for a corrected report
and another independent acceptance replay.

Evidence lives in
`artifacts/game-verification/game-runtime-v1/P00/run-002/replay/`.
The previous report is preserved as `report-before-replay.json`.

1. **[P1] Invalid or unreviewed evidence can satisfy a milestone and pass a later phase.**

   In `scripts/verify_game_plan.py:730`, `_lane_check_status` returns the raw
   check result without considering the report's failed validation or missing
   review. `_any_one_desktop_lane` then accepts it. Separately, lines 674–688
   save lane results before `_apply_prereqs` updates only the aggregate phase
   result, leaving the lane marked `pass` after a prerequisite blocks the phase.

   Four temporary-fixture reproductions all returned `exit=0` for P07 and
   `first_desktop_feature: pass`, despite direct P06 verification correctly
   returning a nonzero exit:

   - Missing P06 reviewer/reviewed revision/replay: P06 `blocked`, exit 2.
   - Incorrect P06 source hash: P06 `fail`, exit 1.
   - Missing P00 prerequisite, milestone requiring P06 checks: P06 `blocked`, exit 2.
   - Same missing prerequisite, milestone requiring the P06 phase: P06 `blocked`, exit 2.

   These are executable reproductions of the verifier, using its existing test
   builders to create temporary manifests and reports. They are not evidence
   that a real P06 or P07 implementation exists.

   Repair `scripts/verify_game_plan.py` and add regressions to
   `backend/tests/test_verify_game_plan.py`. Milestones must consume validated,
   independently reviewed evidence with satisfied prerequisites for the relevant
   lane. Apply prerequisite checks and exact report bindings per lane before
   recording its final result. Preserve valid one-desktop-lane acceptance;
   globally downgrading a valid macOS lane because an unrelated Windows lane is
   absent is not the intended repair. Exercise the common evidence eligibility
   rule across the other milestone policies as well.

   Expected outcome: all four reproductions return nonzero for P07 and never
   mark the milestone `pass`; the existing valid single-lane tests still pass.

2. **[P2] TypeScript runtime validation accepts inherited object-property names.**

   `src/types/gameRuntimeValidator.mjs:30` and `:136` use JavaScript's `in`
   operator for dictionary membership. That includes inherited properties.

   With the existing valid mapping recipe plus an own JSON field
   `"constructor": 123`, Python and JSON Schema reject the unknown property,
   while the TypeScript runtime validator returns `[]` (valid). Calling
   `validate('__proto__', {})` also returns `[]` instead of rejecting the
   unknown record kind.

   Use own-property membership or prototype-free dictionaries for kind lookup,
   schema caching, additional-property checks, and required-field checks.
   Add actual JSON fixtures containing inherited-name keys to the shared
   invalid bank under `contracts/game_runtime/fixtures/parity/invalid/`, then
   run them through all four validators. Add unknown-kind tests to
   `src/types/gameRuntimeValidator.test.mjs`.

   Expected outcome: `constructor`, `__proto__`, and `toString` cannot evade
   unknown-field or unknown-kind checks; ordinary valid records still pass.
   The new constructor probe directly measured Python, JSON Schema and
   TypeScript; it did not separately run that new input through Rust.

3. **[P1 acceptance evidence] run-002 no longer binds the current Python files.**

   `backend/bughunter/game_runtime/__init__.py` and
   `backend/bughunter/game_runtime/contracts.py` do not match the hashes in
   `run-002/report.json`. This review did not edit either file.

   Submitted revision:
   `sha256:63623fc5d2e09a645ebe57eec7f082ea0de0c81d046fa7593f3bca05e8d908a8`.
   Observed revision over the same 100 paths:
   `sha256:c5345932c7415fca6944267c2a46c24ded2c6897a978ce587055874b6b100e7d`.

   `replay/source-snapshot-observed.json` records expected/observed hashes and
   modification times for all 100 files. `replay/verifier-before-review.log`
   shows the real verifier already returning exit 1 for both hash mismatches
   before this reviewer changed the report. After finding the drift, the
   contract suites were repeated: 51 passed, with no source changes during
   that command. That establishes the current command result but does not
   validate the submitted revision.

   The final verifier also found three new files covered by the manifest's
   source directory but absent from run-002's inventory: `targets.py`,
   `knowledge_benchmark.py` and `knowledge.py`, all under
   `backend/bughunter/game_runtime/`. Their observed hashes are saved in
   `replay/source-inventory-omissions.json`. This reviewer did not create or
   modify them. The observed digest above covers the original 100 paths,
   not a complete snapshot including these additions.

   A later integrity check also detected `src-tauri/src/lib.rs` changing
   after the recorded 100-file observation. See
   `replay/review-verification.json`. The revision above is therefore a
   point-in-time observation, not a frozen current checkout. Stop overlapping
   edits or use an isolated checkout for the next acceptance replay.

   Expected outcome: finish repairs, stop editing the accepted source set,
   generate a new run with fresh command logs and hashes, then submit that
   exact snapshot for independent review. Do not edit run-002's original
   author evidence to make old logs appear current. Include the new parity
   validator/test files in the manifest's required source paths so future
   reports cannot silently omit them.

Observed verification:

- Four decisive backend commands: 51 contract/parity tests, 17 fixture tests,
  57 verifier tests and 5 baseline tests passed (130 total).
- Rust: 6 `game_runtime_types` tests passed; the existing three warnings remain.
- `npx tsc -p tsconfig.json --noEmit`: exit 0.
- Supplied revision-zero parity fixture rejected by all four validators in
  the replayed suites; the diagnostic probe separately confirms rejection in
  Python, JSON Schema and TypeScript.
- The fixture's control-lie/oracle test passed in the 17-test fixture run.
- A forged check-log hash was rejected with exit 1 by a fresh temporary report.
- Additional acceptance probes: 3 passed, 7 failed, exit 1. The seven failures
  are one stale snapshot, two TypeScript cases and four milestone cases.

Replay the acceptance probes from the repository root:

```sh
backend/.venv/bin/python artifacts/game-verification/game-runtime-v1/P00/run-002/replay/acceptance_probes.py
```

The script uses temporary report trees and leaves production source and
reports unchanged. Its snapshot case intentionally refers to run-002; it
will remain failed after repairs unless the submitted old files are restored.
For a future run, port the behavioral cases into regression tests and bind
the snapshot check to the new report. Do not rewrite this historical evidence.

Files changed by this review:

- `run-002/report.json`: only `reviewer`, `reviewed_revision` and
  `independent_replay`; author check results and original evidence are retained.
- This review/handoff document.
- New `run-002/replay/` command logs, diagnostic script/results, source
  inventory and metadata diff/verification records.

No production implementation, contract, test-suite or phase-manifest file was
modified by this review. Earlier run-001, codex-review-001 and baseline evidence
remain preserved.

The final real verifier result is `Phase P00: fail`, exit 1. In addition to
the stale/incomplete snapshot, it correctly refuses the review's differing
revision and the two acceptance results that this reviewer cannot confirm.
The manifest still says `P00: ready_for_review` and `P01: not_started`; those
progress labels are not a passing acceptance verdict.

Message for the next implementation agent:

> Continue P00 repair; do not start P01 yet. Read
> `docs/game-lab/P00-run-002-review.md` and reproduce the saved acceptance
> probes before editing. First fix milestone evidence eligibility and apply
> prerequisite/report bindings per lane, retaining legitimate single-lane
> acceptance. Then fix TypeScript own-property validation and add shared
> negative fixtures plus unknown-kind tests. Run the four backend check
> commands, the Rust contract tests and TypeScript compilation. Produce a
> fresh run-003 with logs, source hashes, a complete manifest-bound inventory
> and an accurate report. Keep reviewer/replay fields empty and the status
> ready_for_review until a different reviewer replays that frozen snapshot.
> Preserve all earlier runs and review evidence. P01 may start only after
> independent P00 acceptance returns exit 0.
