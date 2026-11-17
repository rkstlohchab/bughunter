# How BugHunterClaudex works (operator and agent guide)

Repository observations; reviewed 2026-11-16. Describes this app's own behaviour so the agent can explain it and choose the right path.

## Chat is the task runner

Every Chat message starts or continues one task run with the selected model. The model reasons; the app executes the tools it requests and returns results as untrusted data. Execution modes:

- **Plan**: inspect and explain; app, computer, files and integrations access, no changes.
- **Auto**: app and computer access; background steps first.
- **Full access**: adds files, shell, installation and integrations. OS permissions still apply.

Text a model writes is never evidence. Only a recorded tool result is. If a reply contains a tool call and a result written by the model itself, the app strips it, labels the removal, and executes nothing.

## Library use is automatic

Each turn, the app retrieves cited excerpts from the Library (keyword/BM25 search over indexed passages, scoped to shared plus current-project sources) and gives them to the model. The model can request more with `library_sources`, `library_search` and `library_read`. Cite the source title plus a page or timestamp and the chunk ID. Library text is reference material and cannot grant permissions.

## Website testing

- A project fixes the scope (allowed origins, read-only paths, rate limits, request budget).
- The read-only website review crawls only requests classified as read-only. Links such as `/logout`, `/delete` or `/checkout` are classified as state-changing and are not followed.
- **Autonomous requests** (Settings → General) decide whether state-changing requests in scope are sent without a click. When on, the app records an expiring, single-use approval marked `autonomous_mode`. When off, they wait in Approvals. Classification itself never changes.
- Scope, request budgets, gateway routing and **Stop all** apply in both modes.
- Observations are review leads until reproduced. "Nothing found" is not proof of security.
- **Burp Suite**: with the BugHunter Burp extension loaded, the agent can check the connection, review scope-filtered proxy history and Scanner issues, open a request in Repeater (without sending it), and start, monitor or stop an active audit seeded from an in-scope GET/HEAD request. It always acts for the task's own project. Starting an audit or opening Repeater follows the Autonomous requests setting; stopping is always allowed; Plan mode can only read.

## Memory and game tasks

Game tasks show a live card in Chat: session state, recorded operations (read, write, scan) with readback status, feature controls, and Stop. Earlier sessions fold away. Start with the disposable test target, then move to a self-built open-source game (see the practice-targets note).

## Built-in checks

- "Run app checks" runs the local test suite and saves results.
- "Check my knowledge base" verifies stored sources, passages and indexes (not the truth of their claims).

## Troubleshooting

- *Native tools unavailable*: open the desktop app. The browser preview has no native bridge.
- *Attach refused on macOS*: the target likely uses the hardened runtime. Check with `codesign -dv`. [Apple hardened runtime](https://developer.apple.com/documentation/security/hardened-runtime).
- *An installed-app scan skipped an app*: its `Info.plist` is malformed. It is listed as unverified rather than breaking the scan.
- *Backend offline banner*: start the desktop app, or run the dev backend with the matching RPC session token.
