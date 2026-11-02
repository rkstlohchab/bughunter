# BugHunterClaudex — Scoped Staging Quote Integrity

This update builds on `BugHunterClaudex-Burp-Web-Pentest-Price-Integrity-2026-11-01.zip`. All existing Burp, Chat, desktop, knowledge, and game-memory files are retained.

## Added

- **Chat workflow:** `Test price integrity on my staging site` starts a consent-gated quote-only batch for the selected project.
- **Owner-configured staging test contract:** A staging manifest must match a deployment challenge and explicitly say orders/payments are disabled.
- **Fixed request set:** one GET manifest and three POST dry-run quote checks to `POST /__bughunter__/quote`. No user-selected checkout URL, transaction endpoint, arbitrary target, or model-written payload can be used.
- **Evidence:** all completed requests must produce policy-gateway evidence receipts. Quote outcomes are included in the existing source-linked evidence JSONL export, without exporting the challenge.
- **Controls:** scope and exact read-only path checks, project authorisation, manual approval of the batch, cancellation, emergency stop, request limits, no redirects, and no retry after uncertain results.
- **Project setup UI:** staging origin, synthetic SKU, expected price (minor currency units), generated challenge, acknowledgement, and exact allowlisted paths.
- **Fixture:** the existing local shop now exposes a safe, instrumented quote-only API and a staging manifest for automated testing.

The existing read-only production review and the separately authorized Burp Scanner workflows remain unchanged.

## What this cannot verify

- It cannot place orders, apply real discounts, alter real payment totals, or complete production purchases.
- A staging quote changed by a client-supplied price is an **observed non-transactional calculation anomaly**, not proof of a production pricing exploit.
- A server's response saying `order_created:false` alone cannot guarantee no side effects. The operator must deploy the quote endpoint in an isolated staging environment with no actual order/payment integrations.
- The HTTP gateway and official Burp extension still require integration testing on your Mac. No complete macOS/Tauri build was performed in this environment.

## Build and test on macOS

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/build_burp_bridge.sh
bash scripts/verify_staging_quote_sandbox.sh
npm run desktop:build
```

Read `STAGING_PRICE_INTEGRITY_GUIDE.md` before adding the active testing contract to your staging service.

## Checks performed here

- 38 focused backend regression tests passed, including seven new quote-only staging tests.
- Changed TSX files passed TypeScript parser syntax diagnostics.
- Python compilation and shell script syntax passed.
- Full Tauri/macOS build: not available in this Linux environment.
- Official Burp Montoya JAR build: not verified in this environment.
