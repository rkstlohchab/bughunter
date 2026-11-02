# Tor Browser automation integration

## Added

- A local Selenium/geckodriver adapter launching the **installed Tor Browser executable**, with a copied, state-scrubbed Tor Browser profile and a preexisting Tor SOCKS5 service (no direct network fallback).
- Source-bookmark-only browser navigation, links extracted from the rendered DOM, bounded agent-controlled link following, manual navigation within the active source, and capture to the existing Tor research review/Knowledge Library system.
- Desktop Tor research panel for browser executable/geckodriver configuration, optional site JavaScript (disabled by default), starting, navigating, capturing, closing, and viewing link lists.
- New `tor.browser.*` desktop RPC methods and five run-bound agent tools: `tor_browser_open`, `tor_browser_navigate`, `tor_browser_follow`, `tor_browser_capture`, `tor_browser_close`.
- Browser launch, follow, capture and closure events in the local activity log; explicit refusal to use ordinary Firefox or an unspecified executable.
- Optional browser dependency in backend `pyproject.toml` and unit tests with simulated WebDriver.

## Verification

- Tor SOCKS collector and WebDriver-controller tests run with fake local servers / fake browser, **not** a real Tor Browser installation.
- TypeScript TSX syntax parsing performed without Node project dependencies.
- Live browser automation across Windows/macOS/Linux, full Node UI build, Tauri packaging, real Tor/`.onion` access and browser privacy/fingerprint behavior **not tested in this environment**.
- Python full-suite runs blocked by missing `keyring` and other environment dependencies. No claim of a full green suite.

## Limitations

- No login/form submission/click-anything surface is exposed to the agent. Browsing follows GET links on the explicitly saved origin. If JavaScript is enabled, page scripts can make their own requests.
- A SOCKS5 handshake does not prove the proxy is Tor. The installed Tor Browser binary, its profile and the geckodriver release must be compatible.
- The browser session in an agent worker is separate from the desktop view's browser session, though both use the same stored research captures.
- Automating Tor Browser can weaken fingerprint protections; it is not equivalent to stock Tor Browser use.

See `docs/tor-research.md` for setup and risk boundaries.

Project setup now installs the `browser` optional dependency and PyInstaller collects Selenium for the packaged backend. A separate geckodriver executable is still required on the operator's machine.
