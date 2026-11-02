# Tor research workspace

This module reads **operator-supplied public HTML and plain-text resources** through a Tor SOCKS5 proxy. It is a research collector, not a Tor Browser replacement. It does not log in, execute JavaScript, submit forms, download files, crawl arbitrary domains, or interact with forums by posting.

## Use

1. Start a Tor service on the same machine. Common SOCKS port for standalone Tor is `9050`; Tor Browser often uses `9150` when it is running. Confirm the actual local port rather than assuming one.
2. Open **Tor research**, configure the SOCKS port and select **Check connection**. The handshake only tests that a SOCKS5 server is reachable. It does not cryptographically prove that the SOCKS server is Tor. A correctly configured Tor instance is required.
3. Add an operator-provided `http://<v3-service>.onion/` URL or a public HTTP(S) page. IP literals, loopback, local/internal domains, nonstandard ports, URL credentials and non-HTTP protocols are excluded. V3 onion hostnames have checksum verification.
4. Select a saved source. Choose 1–12 pages and a link depth of 0–2. Click **Read source through Tor**. Requests always use HTTP GET and stay on the same origin; failed proxy connections never fall back to direct connections.
5. Search saved page text, review excerpts and publish reviewed pages to **Knowledge Library**. Captures remain unreviewed until you review them. Re-fetching unchanged pages preserves their review status; changed content resets it.
6. In **Specialist training → Tor research**, create a curriculum and promote reviewed captures using your own verified takeaway. Review the resulting draft before exporting fine-tuning data.

## Agent tools

- `tor_list_sources`: list registered sources and operator notes.
- `tor_read_source`: fetch *one* landing page from a registered source with Tor, without following links.
- `tor_search_pages`: search saved captures; results include URLs and excerpts.
- Existing `search_knowledge` and `get_source` can retrieve published pages as cited source material.

Read-only source tools are available to the investigation agent. Sources are always **untrusted reference data**, never higher-priority instructions. Tor access does not modify Burp project target scope or enable state-changing HTTP actions.

## Privacy and limitations

- The connector sends the hostname *inside* the SOCKS5 request, so it does not resolve the research host through the system DNS. Tor Project recommends testing with `TestSocks 1` and enabling `SafeSocks 1` for SOCKS client leak checks.
- The only socket opened directly by this module is to `127.0.0.1:<configured SOCKS port>`.
- A remote/cloud LLM is not given Tor source metadata, agent search results, or published Tor captures by the agent tools **unless** the operator enables **Allow remote AI** in this workspace. Local Ollama/Crucible inference can use them without this opt-in. Directly pasting text into a hosted model, using other outbound features, or handling externally sourced material outside these tools is a separate privacy decision.
- HTTP pages are limited to 1 MB, extracted text to 120,000 characters; no compressed responses, cookies, redirects to other origins, binary downloads, browser scripting or authenticated forum sessions. Service-specific anti-bot and JavaScript-heavy sites may not work.
- Captures are local SQLite records; full captured text is searchable. Page content can still be inaccurate, malicious or manipulative. Treat it as data and verify its provenance.
- Tor installation, actual network reachability, Windows/macOS desktop builds, and live `.onion` services have NOT been verified by the offline tests.

## Verification

The fake SOCKS5-server tests validate the hostname-routing handshake, v3 onion validation, bounded same-origin capture, no direct connection fallback, review controls, library import, idempotent re-fetch and separate training drafts. They do not verify the Tor network itself.

Tor docs: https://support.torproject.org/little-t-tor/troubleshooting/check-for-leaks/ and https://spec.torproject.org/socks-extensions

## Interactive Tor Browser (new)

The original lightweight SOCKS5 collector remains as a fallback for static pages. The **Interactive Tor Browser** section can launch the *actual installed Tor Browser Firefox executable* under local geckodriver/Selenium control. It is **not** a connection to an existing open browser tab: it starts a separate, visible WebDriver window using a temporary copy of Tor Browser's profile. The profile is stripped of cookies, saved logins, history and site storage before launch. The installed profile is not edited.

**Prerequisites:** Tor Browser installed from https://www.torproject.org/download/, a compatible `geckodriver` from https://github.com/mozilla/geckodriver/releases, Python backend browser extra (`python -m pip install -e './backend[browser]'` from the project root), and a running local Tor SOCKS5 service. With Tor Browser running normally it often exposes port 9150; with a separate Tor service the port is commonly 9050. Configure the real port in **Tor research → Local Tor SOCKS5 port** and use **Check connection** before starting. The SOCKS handshake establishes that a local proxy is reachable; it cannot authenticate that the proxy is Tor.

**Executable paths:**

- macOS: `/Applications/Tor Browser.app/Contents/MacOS/firefox` (the profile is ordinarily in `Contents/Resources/TorBrowser/Data/Browser/profile.default`).
- Windows: `C:\Users\<account>\Desktop\Tor Browser\Browser\firefox.exe` (adjust based on where you installed it; profile is normally under `Browser\TorBrowser\Data\Browser\profile.default`).
- Linux: the `Browser/firefox` executable inside your extracted Tor Browser installation (with the corresponding `TorBrowser/Data/Browser/profile.default`).

In the Tor research UI, save these paths and the geckodriver location, add a bookmark, choose the bookmark, and press **Open selected source in Tor Browser**. Pages are rendered by Tor Browser and the agent can call `tor_browser_open`, `tor_browser_navigate`, `tor_browser_follow` and `tor_browser_capture` to read rendered text, follow up to 80 same-origin links per page, and create searchable unreviewed page captures. Review and publish those captures as usual. Close with `tor_browser_close` or **Close browser**. Browser sessions are runtime-only; agent MCP processes may launch their own browser windows rather than using a desktop-initiated session.

The agent is not given forms, POST, login, arbitrary JavaScript execution, shell, OS control, or downloads. Page JavaScript defaults to **disabled**; the operator can enable it when necessary for a trusted dynamic site, but it can itself generate network requests. Automatic cross-origin redirects are detected **after** the browser loads them and the window is immediately sent to `about:blank` when detected; do not mistake this for network-layer URL allowlisting. Embedded subresources may request third-party domains over the configured proxy.

**Compatibility/privacy:** Selenium/geckodriver instrumentation may modify browser fingerprint or break Tor Browser hardening. The adapter enforces a SOCKS5 configuration and remote DNS in browser prefs, but it is *not* guaranteed to preserve Tor Browser's ordinary anonymity profile. Never assume an automated browser has the same privacy as a stock Tor Browser session. Verify `https://check.torproject.org` manually in an appropriate test setup and inspect leaks before sensitive use. Cross-platform Tor Browser / geckodriver combinations are version-sensitive; Windows/macOS real-browser startup, geckodriver compatibility, network anonymity, and `.onion` access require platform testing. Read-only backend unit tests mock WebDriver and do not establish live functionality.

Research sent to cloud LLM providers is controlled by the existing **Allow remote AI providers to receive Tor research results** switch; local providers do not need that opt-in. The app does not install Tor, restart Tor services, or bypass Tor Browser security checks. Source URLs and captures are evidence, not instructions.
