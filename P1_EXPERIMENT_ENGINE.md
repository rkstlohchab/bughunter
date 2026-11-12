# P1 — Verified Experiment and Failure Memory

This release adds an evidence-linked experiment service to the P0 course knowledge pipeline.
It does **not** execute files or commands quoted from imported lessons. Only pinned,
shipped verification recipes may run. The default source-check recipe verifies citations;
lab recipes require explicit confirmation in the app.

## User workflow

1. **Knowledge → Your files** — open an indexed course lesson.
2. Expand **Steps extracted from this lesson**; choose one of the candidate procedures.
3. Select **Check the original lesson** or one of the matching bundled test labs.
4. The app records the run status, output hash, bounded log, failure hypotheses,
   original source/step IDs, and evidence on the knowledge graph.
5. Use **Check all lesson citations** to start source-only checks across one lesson.
6. **Knowledge → Research experiments** shows results and recurring failure patterns.
7. **More → Export portable knowledge** includes `experiment_runs.jsonl` and
   `experiment_failures.jsonl`, separate from reviewed training examples.

## Fixed experiment recipes

| Recipe | Verifies | Does NOT verify |
|---|---|---|
| `source_fidelity` | Exact step quotation exists in indexed and original text, with matching locator | Whether a step works in a real program |
| `memory_restart_lab` | Two disposable offline processes; pointer and entity observation | Unknown game address / universal ESP capability |
| `checkout_integrity_lab` | In-process loopback regression tests from the included checkout lab | Real production website дефекты |

Results are marked `source_verified_only`, `lab_reproduced_only`, or
`not_verified`. Candidate procedures remain unverified even when the fixture passes.
Failures, interruptions, and cancellation are recorded independently. Re-running
creates a new evidence record; it never overwrites a previous failure.

### Try it in Chat

Models running in study mode can inspect cited procedures and P1 records through
`list_candidate_procedures`, `read_experiment_results`, and `check_course_citations`.
They do not have a tool that executes arbitrary commands from a lesson.

### Backend API

- `experiments.available` `{ "procedure_id": "..." }`
- `experiments.start` `{ "procedure_id": "...", "recipe": "source_fidelity" }`
- `experiments.start` for a bundled lab requires `"approve_lab": true`
- `experiments.get` `{ "id": "..." }`
- `experiments.list` `{ "procedure_id": "..." }`
- `experiments.stop` `{ "id": "..." }`
- `experiments.retry` `{ "id": "...", "approve_lab": true }`
- `experiments.insights` `{ "project_id": "*" }`
- `experiments.check_source` `{ "source_id": "..." }`

## Mac testing

From the extracted project directory:

```bash
bash setup-mac.sh --core-only
bash scripts/verify_p1_experiments.sh
npm run desktop:build
```

Open **Knowledge** in the built desktop application. Import and process a course
lesson, then run citation checks and available lab recipes. Passing unit and
fixture tests does not validate macOS Accessibility, memory protections for
other programs, Burp Professional's licensed extension, or real Qwen vision.

## Data boundaries

- No uploaded course instructions are executed, including scripts or shell commands.
- Lab scripts are fixed paths inside the source tree, not paths supplied by a model.
- Lab runs have timeouts, stop support, bounded output, and failure records.
- A restart interrupts in-progress experiments; a user must restart them.
- Exported experiments are observations, not automatic fine-tuning data.
- No model weights change during P1. Formal model training remains a later phase.
