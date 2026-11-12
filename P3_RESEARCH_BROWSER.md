# P3 — Resumable Research Browser

P3 adds a persistent, operator-driven browsing queue for manually bookmarked HTTPS and v3 `.onion` sources. The same-origin browser is powered by **the existing Tor Browser WebDriver integration** (not the ordinary public HTTP monitor). All content is external, untrusted source material.

## What the integration does

- A research session stores its queue, visited URLs, captured page IDs, progress, and event history in the local SQLite store.
- Each **Read next page** action reads one page. **Read up to 3** reads at most three pages. A model in study mode can advance an *operator-created* session by one page via `tor_research_next` but cannot resume a login or CAPTCHA challenge.
- The browser stays on the bookmarked origin. Unexpected redirects and driver failures interrupt the session and retain the pending page for retry. Stopped sessions are not restarted automatically.
- A likely CAPTCHA, login, or access-denied screen pauses collection. The user completes any interactive challenge in the visible browser window, then uses **Resume after intervention**. No CAPTCHA solver, automatic account login, or form submission is added.
- Captures are recorded as **unreviewed** Tor pages with content hashes. A source must be marked reviewed before it can be published to Knowledge. Re-capturing changed content invalidates the earlier review, as before.
- P2's public RSS/HTML subscriptions and curated train/eval exports remain unchanged. Research browser captures are NOT automatically eligible as verified training examples.

## macOS setup

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p3_research.sh
npm run desktop:build
```

For browser mode, install Tor Browser and a matching `geckodriver`, start a local Tor SOCKS5 service and configure both executable paths in **Advanced → Tor browser**. The lightweight Tor connector and interactive Selenium browser are separate workflows. Tor Browser's geckodriver compatibility varies by release; the native Mac build and a real Tor session have not been run in the build environment.

## From Chat and Knowledge

1. Save a direct source URL in Advanced → Tor browser, then configure the Tor SOCKS proxy, Tor Browser binary, and geckodriver.
2. Select a source, create a P3 research session (1–25 pages, same-origin depth 0–2), and use **Read next page** or **Read up to 3 pages**.
3. If a challenge appears, complete it manually in the visible Tor Browser session and select **Resume after intervention**.
4. Review the resulting pages under **Sources and controlled browsing**, then publish any approved captures into Knowledge. P3 progress also appears under **Knowledge → Research browser**.

### Test coverage

`tests/test_research_browser_p3.py` covers queue limits, cross-origin exclusion, CAPTCHA pauses, manual resume, interrupted-process recovery, stop, tool visibility, prompt-injection text isolation, and unreviewed capture requirements with a fake browser. Additional existing Tor, P0, P1, and P2 tests are included in `scripts/verify_p3_research.sh`.

### Boundaries

This P3 build supplies a resumable research workflow, **not** a blanket automated crawler or CAPTCHA обход. It does not silently browse arbitrary forums, execute code found in research pages, create accounts, or feed unreviewed forum posts directly into model training. A model hosted remotely only receives Tor research metadata when the existing explicit sharing toggle is enabled.
