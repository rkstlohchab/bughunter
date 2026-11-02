# Autonomous website review — development release

## What's new

- **Workspaces → Web Testing → Start investigation → Autonomous website review**.
- **Chat → Site audit** button; plain-language requests such as "audit my website" open the operator approval dialog.
- A bounded, asynchronous, **real HTTP** crawl using the app's existing scope policy, verified built-in gateway (Burp optional), action log and evidence store. No direct HTTP client or model-generated exploit payload is used.
- Follows hyperlinks only within the approved project URLs and explicitly marked read-only paths, up to 30 pages and depth 3.
- Reviews observed HTTP status, response headers, HTML forms, and link paths. Configuration observations become **unconfirmed, evidence-linked review leads**, not automatic security findings.
- Controls for cancellation, emergency stop, execution history and a single downloadable Markdown report. In-flight tasks become **interrupted** on restart and are never replayed automatically.
- Link exclusions for redirects, unknown or stateful paths, URLs with queries, attachments, off-target pages and non-HTML assets. Cookies and authentication inputs are not supplied automatically.

## How to run on your Mac

1. Extract this ZIP into a fresh directory and keep an independent backup of your previous app data.
2. Run `python3 scripts/setup.py`, `python3 scripts/build_backend.py`, and `npm run desktop:dev`.
3. Create/select a project in **Workspaces → Targets & scope** with an exact `https://` scope and documented test authorization. Use a **staging** target if you need to investigate commerce functionality.
4. Edit the project, open **Advanced limits and permissions**, and specify known **read-only GET paths** (e.g., `/`, `/about`, `/products`, `/public/*`). Never mark a GET path as read-only if it triggers a state change; GET alone is not a guarantee of safety. Keep checkout, cart, logout, sign-in, payment and account actions excluded. You can also add URL exclusions and lower request budgets.
5. In **Settings → Connect tools → Built-in web testing**, choose **Connect built-in web testing**. The app starts its own locally bound policy proxy and verifies HTTP + HTTPS via a dedicated, app-only CA (no system trust changes). **Burp Suite is optional**. If you prefer Burp interception, use its separate setup wizard and verify routing. Connection proof expires after five minutes; reverify as needed.
6. Open **Workspaces → Web Testing → Start investigation** (or **Chat → Site audit**). Choose 1–30 pages, confirm authority and begin.
7. Watch **Agents** for evidence references, check **Reports** for review hypotheses, and click **Download report** from the Website Review panel.

## Deliberate limitations

- This is a **read-only autonomous surface review**, *not* a complete autonomous penetration test. It does not submit forms, manipulate carts or discounts, place orders, fuzz parameters, run Nuclei automatically, exploit vulnerabilities or test user accounts. The app's separate disposable cart lab still supports those predefined local regression checks.
- The crawler does not run page JavaScript or authenticate a web session. Single-page applications may require manual browser captures, and missing headers alone are not proof of exploitability.
- An HTTPS response is collected through the verified built-in/Burp policy gateway. The target and proxy must be accessible from **your Mac**. These actions are not executed by ChatGPT or on Kaggle.
- Rate limits, absolute project request budgets and DNS/private-network rules remain enforced by the existing gateway. A live backend and local app owner are required.
- Source syntax tests and Python backend regression tests are performed in the build environment; **full macOS desktop compilation and live Burp/site execution were not possible here**. Run the integration against a site you control before broad use.

## Technical changes

- `backend/bughunter/website_audit.py`: bounded scoped scanner and Markdown report export.
- `backend/bughunter/adapters.py`: built-in local gateway proof for HTTP/HTTPS without Burp.
- `backend/bughunter/app.py`: desktop-only RPC routes `website.audit.start`, `website.audit.stop`, `website.audit.report`.
- `backend/bughunter/tasks.py`: task-recovery and cancel integration.
- `src/WebsiteAuditPanel.tsx`: responsive Web Testing UI.
- `src/Chat.tsx`: explicit chat-to-audit handoff.
- `src/App.tsx`, `src/Team.tsx`, `src/styles.css`: navigation and task presentation.
- `backend/tests/test_website_audit.py`: mock-gateway, no-external-traffic regression tests.

### Release safety model

The code never treats a downloaded page or an imported prompt as authorization. The user-approved project limits and gateway determine which requests can execute. Ambiguous requests are skipped rather than automatically approved. Every finding is a hypothesis until independently validated against evidence and controls.
