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

## 5. Practising safely

The app includes a disposable local shop and a cart lab with deliberately vulnerable and fixed variants. Use them to practise business-logic tests before touching a real site. Their results verify the method, not any external website.
