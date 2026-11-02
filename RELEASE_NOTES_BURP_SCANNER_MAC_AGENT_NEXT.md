# BugHunterClaudex — Mac Agent + Burp Evidence Integration (31 October 2026)

This update is an **incremental source-code release** built on `BugHunterClaudex-Mac-Desktop-Agent-Burp-Bridge-2026-10-31.zip`. It is not a compiled macOS application and not a claim of unrestricted or end-to-end autonomous system control.

## Implemented in source

- **Burp Scanner issue feed:** In Burp Professional, the Montoya extension subscribes to new Scanner issue notifications, retains at most 200 recent entries, excludes request/response bodies, and exports issue type, severity, confidence and query-stripped URLs only. Both Burp's suite scope and the selected BugHunter project scope must allow an item. Nothing starts a scan in this path. Community Edition may not expose Scanner.
- **Burp HTTP history passive indicators:** The extension exports booleans for HSTS, CSP, frame protection and MIME sniffing protection without exporting header values, cookies, response bodies or URL query strings. The Python backend produces **review-only** observations rather than treating absent headers as proven vulnerabilities.
- **Burp Repeater tab:** Chat can open an already observed, in-scope history entry in Repeater **after human confirmation**; it does not transmit the HTTP request. The extension itself checks Burp scope a second time.
- **Chat prompts:** `Show Burp Scanner findings for my authorized project`, `Show Burp history`, and `Open Burp #72 in Repeater` (replace 72 with a real history ID).
- **Evidence export:** Scanner notifications, passive Burp observations, and file-inspection metadata can be exported with provenance and observation hashes. Scanner findings are explicitly marked as requiring independent verification.
- **macOS app UI agent:** Accessibility read now walks nested controls in a selected app and records exact button labels; planned clicks must match those labels exactly, not arbitrary text elsewhere in the window. Consequential buttons still require manual use. Each AppleScript action has a timeout, and the AI's claim of completion does not mark a task completed until the user reviews and confirms the result. Interrupted approval sessions recover as interrupted on restart.
- **Diagnostics:** `bash scripts/verify_mac_autonomy.sh` checks source/build prerequisites and runs available tests without controlling a program or sending network tests.

## Setup on Mac

```sh
cd ~/Downloads
unzip -o BugHunterClaudex-Mac-Agent-Burp-Scanner-Next.zip
cd bughunter
python3 scripts/setup.py
npm run desktop:dev
```

For Burp Professional, after installing a supported Java JDK:

```sh
bash scripts/build_burp_bridge.sh
```

Then install `integrations/burp/build/BugHunterBurpBridge.jar` from Burp's **Extensions** tab. Set the target scope in Burp and in the BugHunter project. Use Settings to check the *authenticated* Burp connection, which differs from detecting an open proxy port.

## Verified in this container

- Python syntax/bytecode compilation and focused backend regression tests.
- TypeScript/TSX syntax transpilation; the frontend dependency resolution/full Vite build was not available.
- Java bridge syntax compilation against interface-shaped Montoya API stubs only. **NOT** compiled against the official Montoya API JAR, which could not be downloaded from this environment.
- ZIP integrity after packaging.

## Still unverified or unavailable

- Actual Apple Silicon macOS native Tauri build, Accessibility permission prompts, AppleScript UI behavior and live desktop session tests.
- Live licensed Burp Professional integration and official Montoya JAR compile.
- An actual Cheat Engine-controlled game or cross-platform EXE/APK dynamic manipulation; existing native memory bridge has separate capabilities and approvals, but this update does not grant control of arbitrary processes or produce cheats.
- General autonomous operation of the *entire Mac*. The selected app session remains permissioned, time-bounded and auditable; privileged and high-impact changes still need authorization.
- Automatic initiation of Burp active scans or exploitation. Scanner findings are imported **after Burp reports them**.

Use intentionally vulnerable labs or targets you own/are explicitly authorized to test. This software does not silently approve access or bypass macOS privacy protections.
