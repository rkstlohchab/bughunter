# P10 · Evidence casebook and multi-frame game entity tracking

P10 advances P9's two-snapshot checks into source-linked, persistent investigations. It uses previously captured observations and staging receipts; it does not attach to games, make requests to external websites, or automatically solve browser challenges.

## In the app

1. Open **Settings → P9 Evidence verification** to create a P9 comparison or corroborate a staging quote test.
2. Open **Settings → P10 Evidence investigations**. Select a P9 evidence review and choose **Create evidence case** to record its provenance and recommended follow-ups.
3. To study known entity layouts, start the existing local radar (`scripts/run_esp_lab_demo.py`) against the disposable offline game or an accessible process with a verified layout. Click **Capture frame** at least three times while the game state changes, then **Analyze and save timeline**.
4. Inspect the case for individual movement observations, possible identity discontinuities, absent entities, health changes, source fingerprints and evidence caveats.
5. Review or close a case, and export a **metadata-only JSON index**. Raw traffic bodies and memory coordinates are not included in this exported index.

## Backend RPC

- `investigation.p10.create_from_review` — requires `project_id`, `review_id`, optional `title`. References an existing P9 report, never duplicates or replays HTTP requests.
- `investigation.p10.create_timeline` — requires `project_id`, `frames` (3–12 bounded local radar `/state` payloads), optional `title`. Stores only derived statistics and SHA-256 fingerprints.
- `investigation.p10.list`, `get`, `refresh`, `set_state`, `summary`, `export` — durable case history and evidence freshness checks.
- Agent tool `get_evidence_casebook` — **read-only, project-scoped summary**; no ability to create cases, scan memory or approve actions.

A timeline measures **sample-to-sample displacement**, not speed; timestamps and camera calibration are not assumed. It refuses nonnumeric coordinates, duplicate entity IDs, oversized observations and malformed frames. Cross-PID pairs are treated as identity discontinuities, not motion. Changing/replacing a linked P9 report marks its case stale.

## Tests

```bash
bash scripts/verify_p10_evidence.sh
npm ci
npm run build
npm run desktop:build
```

The P7 readiness dashboard also exposes a separately approved `casebook` smoke-test suite when running from extracted source. Its passing status is evidence of local P10 fixtures, not a claim that macOS accessibility, Burp, live game memory, or model inference has been verified.

## Remaining limits

P10 cannot discover arbitrary game entity layouts, generate a calibrated in-game ESP overlay, prove stable pointers across process versions, exercise production checkout endpoints, or bypass third-party CAPTCHAs. It provides more dependable evidence for the existing tools, not universal target automation.
