# P00 run-002 — independent replay request

Date: 2026-11-14. Author: Claude (role C). Status: **awaiting independent replay.**

Codex's [P00 review and handoff](P00-review-and-handoff.md) named four remaining
pieces of work before P00 could be accepted. Three of those are now shipped
(cross-language contract parity through shared executable fixtures, lane-aware
verifier aggregation already landed by Codex and extended by the regen harness,
and a fresh run-002 tied to the actual source snapshot). The fourth —
**independent acceptance replay** — cannot be performed by the author of the
code under review. This document asks the next reviewer to execute it.

## What run-002 contains

Location: `artifacts/game-verification/game-runtime-v1/P00/run-002/`.

| file | purpose |
|---|---|
| `report.json` | Acceptance report; `reviewer` and `independent_replay` are `null` on purpose. |
| `manifest.json` | Per-run manifest with command transcripts, log paths/sha256, source snapshot digest. |
| `phase_manifest.frozen.json` | Immutable copy of the phase manifest at the moment of regeneration, for contract-drift detection. |
| `commands.jsonl` | One JSON line per executed command (test suite, tsc, cargo). |
| `timings.csv` | Per-check duration and exit code. |
| `logs/P00_*.pytest.log` | Raw pytest stdout+stderr for each P00 check. |
| `logs/aux_*.log` | Raw stdout+stderr from `tsc --noEmit` and `cargo test game_runtime_types`. |

`implementation_revision` is `sha256:63623fc5d2e09a645ebe57eec7f082ea0de0c81d046fa7593f3bca05e8d908a8`
over the sorted `path\tsha256` list of 100 files declared in the phase
manifest's `source_paths` (plus the new parity harness files). Git commit
`8187f411` is the baseline reference, but the files are untracked, so a commit
SHA alone would not bind the review.

## What to run

From the repository root (`/Users/raksithlochabb/Downloads/bughunter`):

```sh
# 1. Replay the four decisive check commands exactly as recorded. Capture
#    stdout+stderr for each into run-002/replay/<check_id>.replay.log.
mkdir -p artifacts/game-verification/game-runtime-v1/P00/run-002/replay

(cd backend && uv run pytest -v \
   tests/test_game_runtime_contracts.py \
   tests/test_jsonschema_parity.py \
   tests/test_typescript_contract_parity.py) \
   > artifacts/game-verification/game-runtime-v1/P00/run-002/replay/P00.contracts.replay.log 2>&1

(cd backend && uv run pytest -v tests/test_game_runtime_fixture.py) \
   > artifacts/game-verification/game-runtime-v1/P00/run-002/replay/P00.fixture.replay.log 2>&1

(cd backend && uv run pytest -v tests/test_verify_game_plan.py) \
   > artifacts/game-verification/game-runtime-v1/P00/run-002/replay/P00.report_validator.replay.log 2>&1

(cd backend && uv run pytest -v tests/test_baseline_report.py) \
   > artifacts/game-verification/game-runtime-v1/P00/run-002/replay/P00.baseline.replay.log 2>&1

# 2. Three spot-checks the fresh evidence rests on:
#    a. Oracle under control-lie (fixture lies through its control channel; 
#       oracle must side with memory).
#    b. Verifier under a forged report (hand-build a bad report in /tmp and 
#       confirm the verifier still exits 1).
#    c. One of the five reproduced Python-vs-schema mismatches rejected by all 
#       four languages (pick e.g. mapping_recipe__revision_zero).

# 3. Hash every replay log:
for f in artifacts/game-verification/game-runtime-v1/P00/run-002/replay/*.log; do
  shasum -a 256 "$f"
done
```

## What to update in run-002/report.json

Only these three fields. Nothing else.

```json
{
  "reviewer": {
    "role": "V",
    "name": "<your reviewer name>",
    "id": "<unique reviewer id, e.g. codex-replay-002>",
    "method": "fresh-shell command reproduction against actual source on disk + three spot-checks"
  },
  "reviewed_revision": "sha256:63623fc5d2e09a645ebe57eec7f082ea0de0c81d046fa7593f3bca05e8d908a8",
  "independent_replay": {
    "performed": true,
    "reproduced_results": {
      "P00.contracts": "pass",
      "P00.fixture": "pass",
      "P00.report_validator": "pass",
      "P00.baseline": "pass"
    },
    "artifact_refs": [
      {"path": "replay/P00.contracts.replay.log",         "sha256": "<fill in>", "kind": "replay_log", "scope": "run"},
      {"path": "replay/P00.fixture.replay.log",           "sha256": "<fill in>", "kind": "replay_log", "scope": "run"},
      {"path": "replay/P00.report_validator.replay.log",  "sha256": "<fill in>", "kind": "replay_log", "scope": "run"},
      {"path": "replay/P00.baseline.replay.log",          "sha256": "<fill in>", "kind": "replay_log", "scope": "run"}
    ],
    "disagreements": [],
    "spot_checks": [
      "parity_fixture_rejected_by_all_four_languages",
      "oracle_sides_with_memory_under_control_lie",
      "verifier_exits_1_on_forged_report"
    ]
  }
}
```

If any reproduced result is not `pass`, or if any spot-check does not behave
as expected, record it in `disagreements` and do NOT touch the phase manifest
status — report back for another repair cycle.

## What to update in docs/game-lab/phase-manifest.json

Only if all reproduced results are `pass` and there are zero disagreements:

```json
{
  "id": "P00",
  "implementation_status": "verified",
  "lane_results": {"shared": "verified"}
}
```

Check results in `phases[P00].checks` are already `pass`; leave them.

## Final verifier read

```sh
python3 scripts/verify_game_plan.py \
  --manifest docs/game-lab/phase-manifest.json \
  --reports artifacts/game-verification/game-runtime-v1 \
  --phase P00
# Expected: Phase P00: pass  (exit 0) once reviewer and independent_replay are filled in
```

## Preservation guarantees

- `run-001/` is untouched.
- `codex-review-001/` is untouched.
- `baseline/` is untouched.
- Codex's repairs in `scripts/verify_game_plan.py`,
  `backend/tests/test_verify_game_plan.py`,
  `backend/bughunter/game_runtime/testing/fixture_target.py` and
  `backend/tests/test_game_runtime_fixture.py` are preserved and extended.

## What was NOT done by this pass

- **P01 is not started.** Per the handoff, it is eligible only after P00
  independently passes.
- No commits were made. All changes are in the working tree.
- No independent replay was performed by this author.
