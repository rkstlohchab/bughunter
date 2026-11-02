# BugHunterClaudex P6 — Reviewed Skill Memory

P6 builds on P0 course steps, P1 experiment receipts, P4 investigation plans and P5 study missions. It does **not** fine-tune AI weights or establish that a technique works on a new target. P6 makes reviewed, source-linked methods searchable through all model providers already supported by BugHunter.

## What is implemented

- **Knowledge → P6 Skill memory:** review queue and saved cards, evidence levels, original lesson locators, recorded test IDs, review-due dates and manual source rechecks.
- **Lesson → Steps extracted → Save to skill memory:** creates a candidate card without promoting any claim.
- **Evidence levels:** `candidate` (no passed source check), `source_checked` (matching original quotations verified against the indexed source), `fixture_reproduced` (source verification plus a passed applicable bundled offline test), and `stale` (deleted/changed source, changed procedure steps or revoked Tor review). A failed, newer check supersedes an earlier pass.
- **Review gate:** the operator explicitly reviews and summarizes a card after a current passing source check. A review expires after 1–90 days; renewing an overdue review requires a new passing citation check. Review is not scientific certification or proof on external targets.
- **Model tools:** `list_reviewed_skills` and `inspect_reviewed_skill`. These are read-only, project-scoped, respect the existing Tor privacy gate, and expose evidence level and due dates. Models cannot review or run labs through these tools.
- **Evidence export:** metadata-only JSON with hashes and run IDs, saved locally under BugHunter's `exports/`; original course quotation bodies are omitted.
- **P1 evidence update:** new P1 runs bind to the canonical imported `content_hash` rather than the previous empty legacy checksum. To upgrade old P1 lab/source results, re-run the relevant checks. Old P5 missions may detect source-signature drift and ask for a fresh mission.

## Run on your Mac

```bash
cd ~/Downloads
unzip -o BugHunterClaudex.zip
cd bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p6_learning.sh
npm run desktop:build
```

Usage:
1. Import your course in **Knowledge** and process its lessons.
2. Open a lesson, expand an extracted procedure, select **Save to skill memory**.
3. Run **Check the original lesson**. If a bundled fixture is relevant, run it separately after approving that operation in the desktop.
4. Open **Knowledge → P6 Skill memory**, inspect the evidence, enter a review summary, and click **Confirm reviewed skill**.
5. All supported AI model providers can retrieve a reviewed skill card during a study/agent run by calling the two new read-only tools. Ask the AI to cite the source and distinguish a checked quotation from a reproduced fixture.
6. For stale or overdue reviews, refresh/recheck the original lesson before renewing the record. Export the evidence index locally if needed.

## Limits

- P6 records **reviewed evidence**, not model-weight training. The P2 curated dataset workflow remains separate.
- The bundled fixture tests are not generalized tests of arbitrary games or websites.
- Importing a lesson, saving a card, or writing a review does not execute source-authored code.
- Source checks may take time. Press Refresh once the run completes; there is no background scheduled recheck or autonomous external scanning in P6.
- A source or procedure that changes becomes stale rather than silently inheriting an older verification result.
- Full macOS/Tauri runtime validation requires a Mac; backend focused tests and TypeScript syntax parsing can run elsewhere.
