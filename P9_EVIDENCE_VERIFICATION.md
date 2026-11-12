# P9 · Evidence Verification Workbench

P9 extends P0–P8 with a real read-only comparison workflow across **observed game entities**, **staging checkout quote receipts**, and **P3 research browser interruptions**. No arbitrary game offsets, CAPTCHA solutions, or production checkout триггеры are inferred by P9.

## Desktop integration

Open **Settings → P9 Evidence verification** after selecting a project.

### Game observation comparison

1. Start the existing offline радар-сущностей with `python3 scripts/run_esp_lab_demo.py` (or its `--layout` option for a known, accessible offline process).
2. Click **Capture first from local radar**. P9 requests `GET /state` only from `127.0.0.1:8787` with a bounded, short request and validates numeric entity observations. No remote hosts, external URLs, or redirect targets are accepted.
3. Change the scene or process under your own test conditions, then click **Capture second** and **Compare snapshots**.
4. P9 stores project-scoped counts, hashes, and bounded ID/coordinate/health deltas, not raw complete snapshots. Operator-pasted values are not independently attested. A matching ID across different PIDs does **not** establish a stable entity identity or module offset.

### Burp/staging checkout receipt verification

Run the existing P8/P7 staging quote-only tests through the ШЛЮЗ-П, then choose the task under **Staging price receipts** and press **Verify receipts**. P9 cross-checks every claimed evidence ID against a gateway-origin traffic record in the same project. It also checks the recorded quote response payload and non-transaction flags, not merely the task's self-reported totals. Missing or imported records produce `insufficient_evidence`. P9 sends **no additional HTTP requests** to the staging website.

Results are limited to the dedicated staging quote protocol. They are not evidence that arbitrary checkout endpoints accept or reject price changes.

### CAPTCHA and login interruptions

Under **Research interruptions**, P9 lists P3 sessions waiting for a human in a project-scoped manner. It shows only the blocker category, session ID, capture count and next action. Complete any CAPTCHA or login yourself in the existing browser and use the **P3 Research browser** Resume control; P9 does not solve, обход, or submit challenges.

### Model access

The configured local, Kaggle or subscription study model has **`get_advanced_verification`**, a read-only current-project summary. It cannot manufacture, edit or approve evidence records or issue local game-memory, checkout, or CAPTCHA actions through this tool.

## Implementation

- `backend/bughunter/verification_p9.py` — validation, receipt correlation, review history, bounded loopback radar capture, project isolation, read-only summaries.
- `src/VerificationP9.tsx` — UI for two snapshots, actual localhost capture buttons, receipt review, interruption inventory, and evidence history.
- `backend/tests/test_verification_p9.py` — tests against observed data, loopback HTTP, invalid data, forged receipts, project isolation and model access.
- `scripts/verify_p9_advanced.sh` — repeatable regression verifier.

## Mac final acceptance

```bash
cd ~/Downloads
unzip -o BugHunterClaudex.zip
cd bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p9_advanced.sh
npm run desktop:build
```

P9's Python tests and TS syntax were run in the build container, but native Mac/Tauri compilation and actual Mac memory permissions, Burp Professional integration, and Tor interaction remain unverified until run on your Mac.
