# Authorized website testing playbook

Authored synthesis; reviewed 2026-11-16. For sites you own or are explicitly authorized to test. Methodology follows the OWASP Web Security Testing Guide. [OWASP WSTG](https://owasp.org/www-project-web-security-testing-guide/).

## 1. Scope before traffic

Record the authorization, the exact origins, and the paths that are safe to read. Prefer staging or a disposable fixture for anything that writes (carts, orders, accounts). Keep requests per second low on production. The app enforces scope and budgets, but the operator decides what is authorized.

## 2. Map read-only first

Start with the read-only review: pages, scripts, forms, API endpoints, response headers, and cookies. Capture through the gateway or Burp so every request has evidence. Note which endpoints change state; they need a separate, deliberate test plan.

## 3. Test by hypothesis, one change at a time

For each lead, state the expected secure behaviour, then send one controlled variation:

- **Access control (IDOR/BOLA)**: replay the same request with a second test account. Only the owner's data should come back. [OWASP API1](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/).
- **Business logic**: price, quantity, coupon and order-of-steps changes. The server must recompute totals. [WSTG payment functionality](https://wstg.owasp.org/latest/4-Web_Application_Security_Testing/10-Business_Logic/10-Payment_Functionality/).
- **Input handling**: reflected or stored output encoding, and server-side request fetching (SSRF) restricted to intended hosts.
- **Session management**: logout invalidation, cookie flags, and session fixation. [OWASP session cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## 4. Evidence standard

A finding needs the exact request, the response, the account or session used, the expected versus observed behaviour, and a reproduction. Passive header observations and scanner notifications are leads, not confirmed vulnerabilities. A secure control that holds is a valid, reportable result.

## 5. Working through TBHM

The Library holds the Bug Hunter's Methodology Live decks (Day One Recon, Day Two Application Analysis) and their talk recordings. The app turns them into a tracked workflow:

- `tbhm_methodology` lists the phases (scope and tracking, recon, application analysis, vulnerability classes, reporting) with this project's coverage. With a `section_id` it returns that section's checks, its traffic rule, the tools that serve it, and cited slide and talk excerpts. Cite deck title and page.
- `tbhm_progress` records each section using the deck's mind-map colours: in progress (orange), no findings (green), findings (red, with a note or evidence IDs), or not applicable.
- Traffic rules: passive sections use third-party data only; browse sections use normal in-scope requests; active sections (brute force, scanning, fuzzing, vulnerability tests) stay within the recorded scope, the program's rules and rate limits, and the app's request workflow.

## 6. The user's own site: code review and live monitoring

- `route_review action=review source=<folder or repo URL>` lists every route it recognises and flags missing auth, missing ownership checks, unvalidated input, injection and fetch sinks, unsafe uploads, open redirects, unthrottled auth routes, mass assignment, permissive CORS and hard-coded secrets, each with file:line. Read the handler to confirm, then confirm on the live site with the flag's live check.
- `site_monitor action=add_source path=<log file or folder>` then `ingest` reads access logs from any host and reports attacks by TBHM technique, with sample requests, whether any attack request got a 2xx, the code fix and block rules. Logs show request lines only, not bodies.

## 7. Practising safely

The app includes a disposable local shop and a cart lab with deliberately vulnerable and fixed variants. Use them to practise business-logic tests before touching a real site. Their results verify the method, not any external website.
