# P4 — Knowledge Gap Planner and Evidence-Driven Study Loop

P4 adds a persistent, source-grounded **Learning investigations** section under Knowledge. It reuses the existing P0 source and procedure index, P1 experiment evidence, P2 reviewed sources, and P3 browsing sources. It does not download, execute, or silently trust course instructions.

## Functionality

- Create a learning goal and locate indexed passages by the existing lexical / optional local embedding retrieval pipeline. Each match keeps its **source ID, chunk ID, locator, and excerpt**.
- Match relevant course procedures by a transparent token overlap with the goal; preserve their candidate status and cite their original source.
- Explain outstanding gaps: absent knowledge, missing procedural citations, unrun/failed source-fidelity checks, unavailable or unrun supported disposable lab recipes, and missing target-specific validation.
- Keep a durable plan with a timestamped change history; refresh it from current sources/experiments, so deleting a source revokes its previous support.
- **Run next safe check** executes *one* bounded P1 source-fidelity test or reports what needs human review. It cannot run downloaded scripts, arbitrary commands, live website actions or even the bundled executable lab recipes. The latter remain in the separate P1 interface and require explicit approval.
- Present cited evidence, related procedures, failed tests and history in the existing Knowledge page. Models can use `list_learning_plans`, `create_learning_plan`, `inspect_learning_plan`, and `advance_learning_plan`. Tool access works through existing provider routing including Ollama, Kaggle/openAI-compatible, and supported subscriptions.
- Keep reviewed Tor evidence behind the existing per-provider remote-sharing choice. Unreviewed Tor captures are excluded from the P4 retrieval result.

## What P4 does not do

A plan is not a new neural-network skill, independent verification of real-world techniques, unrestricted autonomous browsing or automatic software engineering. The model has access to the evidence ledger and can propose next steps, but independent target-specific validation must still happen. P4 also does not automatically solve CAPTCHAs. The P3 browser continues to pause for operator intervention.

## Mac instructions

Extract `BugHunterClaudex.zip` under `~/Downloads`, then:

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p4_learning.sh
npm run desktop:build
```

Open **Knowledge → Learning investigations**. Enter a specific goal (e.g. `Verify stable pointer resolution across offline game restarts`), then choose **Investigate**. Inspect citations and gaps. Select **Run next safe check** to run at most one citation-only P1 experiment. Use **Recheck evidence** after reviewing new sources or running an approved experiment separately.

## API

- `learning.p4.list` `{project_id}`: summaries only
- `learning.p4.create` `{goal, project_id?, procedure_id?}`: evidence-backed plan
- `learning.p4.get` `{id, project_id?}`: source references and missing evidence
- `learning.p4.refresh` `{id, project_id?}`: reconcile and record substantive changes
- `learning.p4.advance` `{id, project_id?}`: one citation-only check, or a review blocker
- `learning.p4.check_citations` `{id, procedure_id, project_id?}`: explicit P1 source quotation check
- `learning.p4.archive` `{id, archived?, project_id?}`: archive or restore without deletion

All plans are project-scoped. The model sees P4 tools with the same study-mode restrictions as earlier P0–P3 library tools. Retrieved source text is untrusted evidence, not executable instructions.

## Verification

The included `scripts/verify_p4_learning.sh` runs focused backend tests, validates Python syntax and checks the setup script. The new P4 tests cover durable plans, real indexed excerpts, source-fidelity results, single-step advancement, project isolation, source deletion, Tor review/privacy filtering, and invalid operations. macOS/Tauri native operation is not verified by this Linux build environment.

### Reviewed research leads

When a P2 research update title matches a learning goal, P4 records its discovery ID as an **unreviewed lead**. It never copies an unreviewed article into the investigation's cited evidence or executes instructions found inside. Use **Knowledge → New research** to inspect the actual update and decide whether to add it to the library.
