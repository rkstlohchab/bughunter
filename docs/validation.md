# Validation record

Executed locally on macOS Apple Silicon on 2026-10-29. All target requests in verification used owned loopback fixtures.

## Automated verification

- **80 Python tests passed.** Coverage includes actual PDF/video extraction, citation locators, YouTube caption fixtures, corrections/deletion/interrupted ingestion, vector isolation, scope/DNS checks, approval consumption and concurrent claims, account separation, traffic imports, lab vulnerable/fixed/display-only controls, provider error/limit handling, local-only restrictions, model capabilities, redaction, and evidence confirmation.
- Gateway integration tests start real mitmproxy processes and HTTP servers. They verify no-ticket blocking, exact-once sending, removal of internal headers, trusted response recording, out-of-scope redirects/background blocking, held browser writes, stop while pending, and HTTP/HTTPS local routing probes.
- A real managed Chromium form submits through the gateway, waits for human-equivalent approval plus release, and receives the actual response exactly once. This caught and fixed a mitmproxy connection-object lifecycle bug and a gateway certificate readiness race.
- **5 Playwright UI tests passed.** These cover scoped project creation, separate approval/send, rendering untrusted source text/responsive layout, and keeping approval/emergency-stop controls available during a waiting browser workflow. One uses a real isolated Python backend to import/search a lesson, propose and review a coupon test, execute the local shop request, assess/export evidence, and save the finding as a lesson. The real workflow also rejects unauthenticated/untrusted-origin RPC and checks the verified observation node in the graph.
- **2 Rust tests passed.** A stop RPC overtakes a slow request; backend exit rejects a pending call without replaying it.
- TypeScript and Vite production build passed.
- The frozen Python sidecar passed a stdio ingestion → citation retrieval → approval → local shop → report smoke test. Its packaged MCP server initialized and served a scoped tool call without requiring model inference. Its packaged gateway passed real HTTP and HTTPS loopback probes.
- The 126.55 MiB native `.app` bundle built successfully. A launch with isolated data confirmed the actual WebView connected through Tauri IPC and initialized its bundled backend. Closing the native process released the backend owner lease. The local bundle's ad-hoc code signature was verified; it is not notarized.

## External integration status

Claude Code is installed and detected a signed-in `claude.ai` Pro account. A live, metadata-only smoke run was attempted through the unmodified CLI. Anthropic rejected it with a subscription/billing availability error before any MCP call. The app preserved the error and session ID and did not attempt API fallback. Successful authenticated reasoning and live MCP use remain unverified until that provider issue is resolved. Mocked CLI tests verify the configured tool restrictions, session resumption, failure, and limit handling.

Burp Suite was not available for a live desktop/extension test. Real gateway and Chromium tests do not replace verifying the user's dedicated Burp listener, upstream proxy, certificate trust, and extension configuration. Real-target execution remains gated on route verification.

Optional billed APIs, live YouTube retrieval, Subfinder, Nuclei, OCR language packs, and whisper model inference were not exercised against remote services in this validation. Their dependencies/configuration and partial/unavailable states are explicit. No API purchase, remote scan, or bounty submission was made.

## Repeat the checks

```sh
backend/.venv/bin/python -m pytest -q backend/tests
npm run test:ui
cargo test --manifest-path src-tauri/Cargo.toml
npm run build
python3 scripts/package.py
python3 scripts/smoke_backend.py
```

Gateway tests require the backend `tools` extra. The browser test requires its Playwright Chromium installation. UI tests start an isolated backend and delete its temporary data; they do not change the desktop library or run Claude inference. Playwright preserves the verified report screenshot in `test-results/`.
