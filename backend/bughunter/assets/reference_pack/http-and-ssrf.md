# HTTP destination handling and SSRF review

Authored synthesis; reference reviewed 2026-11-11.

## Method: validate the connection actually used

Review schemes, host parsing, ports, redirects, DNS answers, and retries together. Reject unexpected destination classes and ambiguous encodings. When policy depends on a resolved address, the client must connect to that checked address while preserving hostname-based TLS verification. An unchecked second DNS lookup can defeat an earlier check. Reapply destination policy to redirects and fallback connections. See [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

For bounded reproduction, use a local receiver with a unique request marker and timestamp. Correlate the receiver event with the triggering request; a browser fetching a resource is not evidence that the application server fetched it. Differentiate response reflection, DNS resolution, a TCP connection, and a completed HTTP exchange.

## Repository-specific checks

`canonical_url` rejects encoded path separators, relative segments, credentials, and unsupported schemes. Scope checks compare scheme, authority, and path boundaries. A mixed DNS answer set containing an excluded private address is rejected by policy tests. Those checks should not be described as proof of all gateway connection behavior; test the actual transport independently.

The disposable commerce transport binds to the exact active loopback port, checks its nonce, and consumes a queued action ticket before transmission. A manually set `lab_project` flag or JSON `lab_evidence` marker alone does not select that transport. Never treat an untrusted response marker as the source of destination authority.

## Evidence and negative controls

Keep the initial URL, normalized URL, resolved destination, redirect chain when applicable, session, receipt, and observed receiver marker. Stop at the earliest missing correlation. A blocked connection is evidence of that particular check only, not a general verdict on every URL parser or network path.
