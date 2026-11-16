# P00 independent review and next-agent handoff

Review date: 2026-11-14. Reviewer: Codex. Verdict: **P00 is not accepted yet.**

The supplied CLI originally returned `Phase P00: pass`, exit 0, and its 75
reported Python tests really passed on this Mac. However, those tests did not
prove the phase's contract-parity requirement, and the verifier accepted
incomplete or contradictory evidence. The phase manifest now records a failed
review instead of verified completion. The original `run-001` report and logs
were preserved.

## What was independently checked

- Original four-file Python suite: **75 passed**.
- TypeScript: `npx tsc -p tsconfig.json --noEmit` exited 0.
- Rust: `cargo test --locked --manifest-path src-tauri/Cargo.toml game_runtime_types`
  passed its **2 tests**, with the three existing warnings outside these changes.
- After the repairs below, the four-file Python suite: **113 passed**, no skips,
  in 25.06 seconds. This includes 46 verifier tests and 17 fixture tests.
- Five contract mismatches were independently reproduced: Python accepts mapping
  `revision=0`, `revision=true`, `revision="2"`, `schema_version=-1`, and a game
  target missing `modules` and `capabilities`. The corresponding JSON Schemas
  reject these records.
- On temporary copies, the old verifier returned 0 for the original contradictory
  report, a report stripped of check evidence, and a corrupt duplicate check
  followed by a valid duplicate. The repaired verifier returns 1 for all three.

Evidence, exact command results and before/after copies are in
[`codex-review-001`](../../artifacts/game-verification/game-runtime-v1/P00/codex-review-001/).
The structured review is
[`review.json`](../../artifacts/game-verification/game-runtime-v1/P00/codex-review-001/review.json).
It is a review record, not a replacement acceptance report.

## Repairs made in this pass

- [`scripts/verify_game_plan.py`](../../scripts/verify_game_plan.py): validate
  report identities, plan/contract revisions, source/input hashes and source
  inventory; bind dirty implementations to a source-snapshot digest; reject
  missing evidence, malformed exit codes, duplicate/unknown check IDs and
  contradictory independent replay. Enforce explicit milestone prerequisites,
  exact prerequisite-report bindings and conditional mobile checks. Reject
  dependency cycles and prevent evidence paths escaping their declared root.
- [`backend/tests/test_verify_game_plan.py`](../../backend/tests/test_verify_game_plan.py):
  add regression cases for those false-acceptance paths, frozen manifest
  comparison, omitted source files, mixed target identities and platform coverage.
- [`fixture_target.py`](../../backend/bughunter/game_runtime/testing/fixture_target.py):
  use a bounded response queue with a dedicated stdout reader; bound RPC response
  waits; make shutdown include the response wait in its deadline; prevent a timed
  out channel from consuming a late reply as a new result.
- [`test_game_runtime_fixture.py`](../../backend/tests/test_game_runtime_fixture.py):
  test a child that reads the command but stalls instead of replying, and verify
  termination/reaping plus rejection of further calls on a timed out channel.
- [`phase-manifest.json`](phase-manifest.json): correct P00's status, record the
  review and declare the source files that a fresh acceptance report must cover.

The Python/TypeScript/Rust contract bindings were **not repaired in this pass**.
Their remaining mismatch is a reason to keep P00 unaccepted, even though the
existing contract tests pass.

## Remaining acceptance work

1. **Implement actual cross-language contract parity.** Run the same valid and
   invalid fixtures through JSON Schema, Python, Rust and a TypeScript runtime
   validator. Searching source for `CONTRACT_VERSION` and compiling type aliases
   are insufficient. Cover required fields, unknown fields, integer bounds and
   coercion, address/hash/offset formats, nullable fields and enum values. Fix
   Python's reproduced acceptance of invalid records and Rust's unconstrained
   strings/defaulted fields. Keep schemas and bindings consistent; do not loosen
   the schemas simply to turn the current tests green.
2. **Finish lane-aware report selection and aggregation.** The current verifier
   still selects one latest report per phase. It conservatively blocks a
   full-scope milestone requiring both desktop lanes instead of treating one
   report as proof of both. Implement selection by phase/lane/exact target;
   distinguish any-one-desktop milestones from both-platform release acceptance;
   bind downstream inputs to the selected report hashes. Test missing Windows
   evidence, mixed device/build reports, stale controller evidence and conditional
   mobile release acceptance. This is remaining verifier work, not an accepted
   full-platform verification claim.
3. **Regenerate acceptance evidence after the fixes.** Preserve `run-001`; create
   a fresh numbered run with raw logs. The current report refers to commit
   `8187f41`, but the implementation files are untracked and absent from that
   commit. Use the actual implementation commit, or `sha256:<digest>` from
   `source_snapshot_digest(report["artifacts"])` with a complete hashed source
   inventory covering P00's `source_paths`.
4. **Obtain a real independent acceptance replay.** The existing report's
   `independent_replay.reproduced_results` still records the fixture as blocked.
   Its acceptance cannot be repaired by changing that word to pass. A different
   reviewer must execute the decisive checks on the final source revision and
   save hashed replay logs. Only then update the acceptance status.

The fixture's value oracle genuinely reads a separate process through the OS/
debugger path. Its restart helper currently learns fresh region bases from
fixture identity. That is useful fixture infrastructure; it is not P05 proof
that a saved production pointer resolver survives restarts without supplied
solution addresses. Keep that distinction in reports and later tests.

The baseline records 49 pre-existing full-backend failures. They were not repaired
or concealed by this review. Preserve the baseline and track those failures
separately; do not rerun its script against changed code and label the output a
pre-change baseline.

## Evidence format for the next run

- `input_artifacts`, `artifacts` and per-check `artifact_refs` contain objects
  with `path`, `sha256` and an informative `kind`.
- Source/input paths default to the repository root; check/replay paths default
  to the run directory. Use explicit `scope: "repository"` or `scope: "run"`
  where needed. Source snapshots bind current repository paths.
- Keep a hashed immutable `kind: "phase_manifest"` input copy in the run. The
  verifier compares its contract with the current manifest while ignoring
  progress-only fields; changing a phase's status need not invalidate a frozen
  contract input.
- `owner` and `reviewer` need non-empty, distinct `name` and `id` fields.
  `reviewed_revision` matches `implementation_revision`.
- `independent_replay` contains `performed: true`, exact check results in
  `reproduced_results`, and hashed `artifact_refs`. Explanatory prose belongs
  alongside the result, not inside a value that should equal `pass`.
- A report awaiting review uses null reviewer/reviewed-revision/replay fields
  and must not be reported as verified. Empty `blockers` is valid.
- A phase with conditional mobile checks declares boolean
  `mobile_support_advertised`; advertising mobile support makes those checks
  required. Full-scope acceptance always requires the mobile milestone.

## Message to give Claude

```text
Continue from docs/game-lab/P00-review-and-handoff.md.

Do not start P01 yet: Codex independently rejected P00's previous acceptance.
The original 75 tests pass, and 113 tests pass after Codex's verifier/fixture
repairs, but the shared contract requirement remains unmet.

Read the review and phase manifest, inspect the existing team claims, then claim
the exact files you will edit. Preserve Codex's verifier and fixture timeout
repairs and preserve the original run-001 evidence.

First fix JSON Schema/Python/TypeScript/Rust validation parity with shared
executable fixture tests, including the five reproduced mismatches. Then finish
lane-aware acceptance report selection and milestone aggregation. Produce a
fresh report tied to the exact final source snapshot, with raw test logs and a
complete source inventory. Arrange an independent replay; do not mark your own
work verified or reuse the old review result.

Return the changed files, exact commands/results, remaining failures and the
new report path. P01 becomes eligible only after P00 independently passes. P01
then implements explicit course collection bindings, task-based retrieval,
correction reuse and the 30-answerable/10-negative evaluation from the plan.
```

Reproduction commands:

```sh
# From backend/
uv run pytest -q tests/test_game_runtime_contracts.py tests/test_game_runtime_fixture.py tests/test_verify_game_plan.py tests/test_baseline_report.py

# From the repository root; currently expected to reject stale run-001 evidence.
python3 scripts/verify_game_plan.py --manifest docs/game-lab/phase-manifest.json --reports artifacts/game-verification/game-runtime-v1 --phase P00
```

`before/` and `before-sha256.json` in the review directory preserve the five files
changed from their prior state. `changes.patch` records this pass, including this
new handoff. Restore only those copies after checking for later edits; do not use
a repository-wide reset, which would remove Claude's unrelated uncommitted work.
