# BugHunterClaudex — Chat autonomy and verifiable desktop actions

## Purpose

Builds on `BugHunterClaudex-Verified-Mac-Memory-and-App-Control-2026-11-01.zip` without deleting existing project code. This is an **incremental source-code build**, not a verified native Mac executable or a claim of unrestricted computer control.

## Implemented in this revision

- A natural-language **Burp combined review**: an authorized Chat request verifies the bridge, reads real in-scope HTTP proxy-history records, reads in-scope Burp Scanner notifications, aggregates passive header observations, and saves one task with per-step execution receipts. No new scan is launched. Burp itself still needs the locally installed Montoya extension.
- Existing explicit commands continue handling active scan start/status/stop; the review workflow cannot silently trigger those actions.
- Reviewed Burp metadata and Scanner notifications can be exported from Knowledge with source labels and SHA-256 observation hashes; both export paths have tests.
- Mac-app automation records a SHA-256 digest of each observed UI state alongside the action type and a native-client-reported execution status. It does **not** store typed text, window contents, screenshots or model prompts in its task receipts. Native-client reports are not independently attested by the backend.
- The Mac planner by default forwards **only selected Accessibility control labels**, not arbitrary static page text. Optional consent for visible screen text is available in the Mac app control dialog. Screenshots stay local and are not sent to the model.
- Desktop agent closes failed or exhausted sessions instead of allowing an unlogged Continue action. Existing macOS approval, focus checks, one-app scoping, native action budgets and Stop controls remain.
- New `scripts/verify_chat_autonomy_on_mac.sh` checks dependencies, a focused regression suite, local memory self-test, frontend build, Cargo check and Burp extension compilation on **your actual Mac**. It intentionally does not operate target programs.

## Still NOT claimed / unsupported without live verification

- Unrestricted control of all macOS programs, access to credential dialogs, Terminal execution, or arbitrary system modification.
- Guaranteed Cheat Engine integration inside Wine/CrossOver, process attachment to protected games, anti-cheat bypasses, ESP creation or generic unlimited-money cheats.
- Independent proof that every Burp Scanner notification is a genuine vulnerability, or arbitrary active exploitation outside authorized scope.
- A successful official Burp Montoya JAR build or macOS Tauri bundle in this Linux environment.
- Automated provider-safety bypass or extraction of a frontier model's original neural weights.

## Mac setup and verification

```bash
cd ~/Downloads
unzip -o BugHunterClaudex-Chat-Autonomy-Verifiable-Mac-2026-11-01.zip
cd bughunter
python3 scripts/setup.py
bash scripts/verify_chat_autonomy_on_mac.sh
npm run desktop:dev
```

To test Burp, build the extension and install its JAR in Burp Professional; select an authorized lab project and ask: `Use Burp Suite to review the website security findings`.

To test Mac GUI control, use Chat -> Control a Mac app, select an ordinary non-sensitive `.app` bundle, grant macOS Accessibility permission, review the planned task, and watch the tool receipts. For private apps, leave visible-text sharing disabled.

For memory inspection, choose your **own offline test process** and explicitly approve any attach or write. The built-in macOS memory helper is not a Cheat Engine installation.

If any test fails, capture its terminal error and the app's task ID; neither errors nor failures are suppressed.
