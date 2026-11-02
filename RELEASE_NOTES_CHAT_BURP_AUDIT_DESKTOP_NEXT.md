# BugHunterClaudex — Chat-controlled Burp audit and Mac desktop navigation

**Source-code release, 1 November 2026.** Builds on `BugHunterClaudex-Mac-Agent-Burp-Scanner-Next.zip` without resetting user databases or existing features.

## Newly implemented

### Real Burp audit control from Chat

- Chat recognizes `Run Burp scan #42`, `Burp scan status`, and `Stop Burp scan`.
- An active scan requires an **explicit confirmation in Chat**, a website project with recorded authorization, matching include/exclude scope, an explicitly permitted read-only seed path, and an existing Burp history entry (GET or HEAD, without query parameters).
- The official Montoya bridge now implements authenticated localhost audit start/status/stop endpoints. Burp itself executes the audit using the Montoya `Scanner.startAudit` API and a legacy active-audit configuration. This implementation does not supply arbitrary exploit payloads and does not crawl a whole site.
- Only one audit can be tracked by the extension at a time. Burp's local extension retains no request bodies, cookies, headers or query secrets for its response APIs. Chat can report the tracked task's request count, error count, and scanner issue count without claiming the issues are independently verified.
- **Warning:** Active Burp audits can send many requests and may modify site data even when their seed is a GET request. The workbench's normal per-project request budget is NOT a hard limit on requests sent by Burp's own audit engine. Configure Burp's resource pools and scan scope appropriately. The Stop command calls Montoya `Audit.delete()`—this deletes the scan task, so retain any required results before stopping. The existing separate Scanner issue ledger may retain observed notifications.

### Safer selected-app Mac automation

- Reads Accessibility tab labels and supports selecting an actually observed tab, useful for tab-driven desktop tools.
- Reads named text inputs and can focus a currently observed non-sensitive text field before typing. The native operation checks whether a text field is actually focused and refuses protected inputs.
- A selected Mac app must become frontmost after launch before the session begins; every action still checks focused application identity.
- Added a native blocklist for consequential button labels in addition to the backend plan validation. Read-only sessions remain read-only.
- The model is still limited to a user-selected application, a 15-minute session, and 40 native actions, with a separate step cap in Chat. Accessibility permission is requested by macOS, not bypassed.

### Cheat Engine and local game work

Existing memory integration, including Mac native read/scan tools and the approved Cheat Engine MCP handoff, is **preserved**, but this release does **not** add or verify arbitrary game modification, universal Cheat Engine operation under CrossOver, online-game cheat automation, or anti-cheat bypasses. On Apple Silicon, Cheat Engine Windows executables and native macOS process debugging are different tool paths.

## Commands (Mac)

```sh
cd ~/Downloads
unzip -o BugHunterClaudex-Burp-Audit-Mac-Autopilot-Next.zip
cd bughunter
python3 scripts/setup.py
npm run desktop:dev
```

In a separate Terminal session, after installing Java 21, compile the Burp Pro bridge:

```sh
bash scripts/build_burp_bridge.sh
```

Load `integrations/burp/build/BugHunterBurpBridge.jar` through Burp Suite Professional > Extensions. Configure your target in BOTH Burp scope and a BugHunter authorized website project. Use Chat to read Burp history and obtain a real history ID before requesting a scan.

To check build prerequisites and regression tests:

```sh
bash scripts/verify_mac_autonomy.sh
```

## Verification and limitations

- Backend source compiles, and focused Python tests exercise approved start, blocked/out-of-scope starts, status, cancellation approval, Chat routing, and desktop action validation.
- Java code compiled against local API-shaped stubs. **Not** compiled against the official Montoya JAR in this container because the Maven host could not be resolved.
- TypeScript syntax transpilation passed. The full Vite build is unavailable because npm's offline cache lacks dependencies.
- No native Tauri/Cargo compilation, Apple Silicon Accessibility test, live Burp Professional audit, or actual Cheat Engine session has occurred here.
- This is **not unrestricted computer control**. A selected-app interface with approval boundaries is safer and more testable than giving an AI unrestricted process, shell, and credential access.

## Safety and privacy

Test only websites you own or have explicit permission to assess. Active scanning may create or alter data. Burp controls are bound to localhost with a local restricted token, and no outside application can widen project scope by injecting instructions into a web response or knowledge file. Avoid scanning accounts, payment endpoints, or live production systems without a specific agreed testing window.
