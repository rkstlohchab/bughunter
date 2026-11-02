# BugHunterClaudex — Burp Web Security & Price Integrity

## What this version adds

- New chat workflow, `web_pentest`, for an **authorized, read-only assessment** of an owned website's recorded Burp traffic. It imports real scoped HTTP history, Burp Scanner notifications, pricing/checkout parameter *names*, and header-presence observations. It produces review leads, not claims of exploitation or proof of a pricing flaw.
- Burp companion extension now sends a strict allowlist of *pricing field names* (`unit_price`, `total`, `quantity`, `coupon`, `currency`, etc.) in history metadata. It never forwards field values, request or response bodies, cookies, authorization tokens, query values, or customer data. Existing URL sanitization and project-scope checks remain.
- A separate `commerce_lab` chat workflow runs only against BugHunter's own **disposable loopback shop**, with approval. Nine deterministic scenarios are tested on both deliberately vulnerable and corrected variants, and HTTP evidence is recorded.
- Price/checkout review evidence is stored in Tasks and added to the existing source-linked evidence JSONL export with `not_verified` labels and hashes. Burp Scanner issue notifications stay separately labeled as unverified reports.
- Chat shows live progress for the disposable checkout lab and supports cancellation.

## What it does **not** do

- It does not automatically modify prices, checkout state, coupons or payments on live e-commerce sites.
- It does not execute active tests, place orders, spend funds, use payment instruments, or claim that a visible price field is exploitable. Price fields in the request may be fully protected by correct server-side controls.
- It does not prove comprehensive vulnerability coverage or replace professional business-logic validation. Existing Burp active-scanning code remains separately approval-gated and has its own limited scope.
- This source update has **not** been compiled/tested against the official Burp Montoya JAR or macOS/Tauri in this environment.

## Install / build on Mac

Extract the updated archive into a separate folder, back up your existing local data, then:

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/build_burp_bridge.sh
npm run desktop:build
```

In Burp Suite Professional, go to **Extensions → Installed → Add** and load `integrations/burp/build/BugHunterBurpBridge.jar`. Add your owned/test domain to **Burp's target scope**, then capture traffic using Burp's browser.

In BugHunter, create/select an **authorized website project** with the same domain. Chat requests:

- `Find possible price manipulation and checkout validation weaknesses in my website's captured Burp traffic.` — Read-only review of already captured in-scope traffic. No new requests are sent.
- `Test price manipulation in my disposable local test shop.` — Confirm the explicit approval, then review **18 observed HTTP requests** (9 vulnerable and 9 fixed-control observations) in the Tasks tab.
- `Show Burp Scanner findings.` — Read any existing, scoped Burp Scanner notifications; these remain unverified.

To run tests from the repo:

```bash
cd ~/Downloads/bughunter
.venv/bin/python -m pytest -q backend/tests/test_web_price_triage.py backend/tests/test_commerce_audit.py backend/tests/test_burp_composite_autonomy.py backend/tests/test_burp_repeater_and_passive_review.py
```

Depending on your Mac setup, use `backend/.venv/bin/python` if that is where setup created its environment.

## Review methodology

Follow OWASP guidance: all pricing-related state should be derived server-side from trusted catalog and order data. Use backend validation for quantities, eligibility, discounts and shipping; require approval and non-production test accounts for any state-changing checkout exercises.

References: [OWASP Payment Functionality](https://owasp.github.io/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/10-Business_Logic/10-Payment_Functionality) and [OWASP Business Logic Security](https://cheatsheetseries.owasp.org/cheatsheets/Business_Logic_Security_Cheat_Sheet.html).

## Verification performed in build environment

- 50 focused web/Burp/backend regression tests: passed.
- Python syntax check: passed.
- TypeScript source parsing for modified Chat file: passed.
- Full backend suite: not collected because `keyring` is missing from this build environment.
- Burp extension JAR build: blocked because Maven Central could not be resolved in this environment.
- Mac application / actual Burp Professional: not runnable in this Linux build environment.
