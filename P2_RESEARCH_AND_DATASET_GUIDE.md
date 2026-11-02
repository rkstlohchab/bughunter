# P2 — Proactive Research and Reviewed Training Data

This update extends P0 course understanding and P1 experiment evidence. It preserves the existing Chat, Tasks, Knowledge, training workspaces, model settings, and macOS control integrations.

## What changed

### Research-source monitoring

1. Open **Settings → Research sources**.
2. Enter a public HTTPS RSS/Atom feed or static HTML page URL and a recognizable source name.
3. Choose **Check now** for a single read. Enable scheduled checks only when needed; the minimum interval is 6 hours. Scheduled checks run *only while the desktop backend is open*.
4. Open **Knowledge → New research** to review differences. Click **Read observation**, then **Add to my library** or **Dismiss**.
5. An accepted observation becomes an ordinary, cited unverified source. Its original URL, observation time, content hash, and text are recorded. It is **not automatically a verified claim**, executable procedure, or training example.

The monitor supports explicitly added public HTTPS pages and RSS/Atom feeds. It **does not** automatically crawl a whole website, follow external links, sign in, automate CAPTCHAs, or browse `.onion` sites. The separate Tor research workspace remains available. Redirects, private IP ranges, credentials in URLs, and oversized responses are refused. Monitored content is never executed.

Failed checks are recorded with the last error and do not auto-promote anything. The app limits read size to 1 MiB per source, tracks a maximum of 40 subscriptions, keeps no more than 1,500 inbox discoveries, and reads at most 60 feed entries per check.

### Reviewed dataset builder

1. In Knowledge, open a source and choose **Create training example**. Write a checked question and answer, choose Training or Evaluation, and check both the review and training-permission boxes.
2. For examples under **Training lab**, mark the example reviewed and explicitly choose **Allow P2 training export**. It is not opted in automatically. For manually authored examples with no external source use `self:manual`; otherwise use an existing local source ID or a recorded `redteam_trial:` / `tor_page:` reference.
3. Open **Knowledge → More → Training data**. Inspect the counts and excluded reasons.
4. Choose **Export reviewed dataset**. BugHunter saves a ZIP under the local app data `exports` folder, containing:
   - `train.jsonl`: supervised chat examples
   - `evaluation.jsonl`: independent held-out examples, only if you curated them
   - `provenance.jsonl`: source IDs, locators, content hashes, origin, split
   - `quarantine.jsonl`: excluded examples and reasons
   - `manifest.json`: counts and split metadata

All examples require explicit review and training permission. Course passages or research summaries **never become training labels automatically**. Dataset creation does not update model weights or start a training job.

### Split leakage prevention

Related examples from one source or scenario group, plus identical normalized questions across sources, cannot land in both train and evaluation. Mixed-split groups are quarantined rather than silently re-labeled. Exact duplicate prompt-answer pairs are removed. If you have fewer distinct curated sources, the evaluation split can be empty and the manifest reports that it is missing.

## Developer commands

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p2_learning.sh
npm run desktop:build
```

The P2 deterministic tests use local fake feeds; they do not rely on unstable network services. To validate real feeds, add one under Settings and press Check now. Validate the result in Knowledge. A true native Tauri build and live web feed monitoring require Mac testing.

## Implementation references

- `backend/bughunter/research_p2.py` — opt-in watcher, validation, RSS/Atom/HTML parsing, change detection, manual curation
- `backend/bughunter/dataset_p2.py` — training consent, evidence audit, split isolation, deterministic export
- `src/ResearchP2.tsx` — source management, knowledge review inbox, dataset audit/export UI
- `tests/test_p2.py` — replayable monitor, source review, URL security, provenance and leakage tests
- `scripts/verify_p2_learning.sh` — focused P0/P1/P2 regressions

## Known limits

- Only user-supplied HTTPS URLs; no general web crawling, JavaScript, CAPTCHA solving or automated login.
- No content-based semantic near-duplicate clustering yet; normalized question and same-source grouping are enforced.
- Monitoring is opt-in and scheduled only while the app is running.
- A copied source is unverified until independently checked.
- No live macOS/Tauri frontend build or real third-party feed run was performed in the Linux packaging environment.
