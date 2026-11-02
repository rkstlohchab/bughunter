# P5: Bounded Study Missions — Implementation and macOS Verification

P5 integrates P0 course citation indexing, P1 disposable experiments, P2 reviewed research, P3 research browser, and P4 gap investigations into one **evidence-tracked mission**. It does not imply model-weight retraining or target-specific validity.

## Run it

1. From the extracted `bughunter/` project, run `bash setup-mac.sh --core-only`.
2. Run `bash scripts/verify_p5_learning.sh`.
3. Run `npm run desktop:build` on macOS and open the app.
4. Open **Knowledge → P5 Study missions**, enter a goal covered by a previously imported course, then choose **Start study mission**.
5. Choose **Advance one step**. A citation check starts for a matching supported procedure. Choose **Check experiment result** after it finishes; the mission records the exact run ID and source-verified status.
6. If an eligible bundled fixture is offered, choose **Approve and run fixture**. It runs only a pinned P1 offline lab; no commands from lesson text execute. Check the result and inspect failure hypotheses.
7. Export the local evidence manifest with **Export evidence**. The result is saved to the application's local `exports/` directory; the UI displays the absolute path.

## New RPC operations

All RPC methods are desktop-only; models use the narrow `ModelTools` study-tool schema.

| Operation | Arguments | Result |
| --- | --- | --- |
| `mission.p5.list` | `{project_id}` | Scoped mission summaries |
| `mission.p5.create` | `{goal,project_id}` or `{plan_id,project_id}` | Persisted mission with next step |
| `mission.p5.get` | `{id,project_id}` | Status, audit, results, next step |
| `mission.p5.advance` | `{id,project_id}` | Start **one** citation check, observe a result, or pause for review |
| `mission.p5.approve_lab` | `{id,procedure_id,recipe,confirm:true,project_id}` | Run a pinned bundled lab after a separate desktop confirmation |
| `mission.p5.retry_source` | `{id,project_id}` | Explicit source-check retry following failure |
| `mission.p5.stop` | `{id,project_id}` | Stop mission and an experiment started by that mission |
| `mission.p5.export` | `{id,project_id}` | Local, hashed JSON evidence report |

The model can list, create, inspect and advance bounded missions but **cannot call** `mission.p5.approve_lab` or `mission.p5.retry_source` through model tools. External network tests, crawling, and Tor challenge solving are not part of P5 advance.

## Evidence and failure semantics

- All mission steps link to an existing P4 plan and P1 run ID.
- Each P5 mission records a fingerprint of source content **and** its extracted step definitions. If the source is removed, reimported, or altered, the mission becomes `stale` before starting another experiment.
- Results are persisted with recipe, actual run status, limited error information, hypotheses, and verification level.
- A failed experiment results in `needs_review` and will not automatically retry. The operator may retry only the fixed source-fidelity check.
- Maximum 8 citation checks and 3 explicitly approved bundled lab starts per mission; one pending experiment at a time.
- Missions record no arbitrary code copied from external sources, and do not promote inferred techniques to verified target-specific knowledge.
- The evidence export contains IDs, statuses, hashes and audit history, **not** raw source bodies, browser credentials, or customer data.
- After app restart, mission history remains. In-flight P1 runs are marked interrupted by the existing P1 recovery mechanism and the user must inspect the result rather than replaying it silently.

## Unverified areas

The source archive is validated in Linux with focused Python tests and TS parsing. Full Apple Silicon/macOS Tauri build, native memory helpers and actual model-provider inference still require Mac-side testing. A passing bundled test only establishes behavior inside the disposable fixture; it is not independent proof of an imported course's external claim.
