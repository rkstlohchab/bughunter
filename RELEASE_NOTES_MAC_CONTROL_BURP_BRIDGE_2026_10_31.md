# BugHunterClaudex — Mac control + Burp history bridge

This release adds code for **bounded chat-driven macOS UI automation** and an **authenticated read-only Burp Montoya extension**. It retains the existing Cheat Engine / macOS memory MCP agent and its per-action approval queue. These are source integrations, NOT a guarantee that arbitrary games or apps can be fully automated.

## How it works

1. Open Chat. Enter a task and click the new **Control a Mac app** icon (or ask to operate a Mac app).
2. Select a native `.app`, approve transmitting its visible window labels to your configured AI model, and optionally authorize clicks and typing.
3. The app launches the chosen native app, verifies foreground focus, asks the AI to propose one action based on the visible window/button names, executes that action through the native Tauri command and repeats with an updated UI observation. It stops on lost focus, permissions failure, unexpected action, loop, task cancellation, time expiry or action limit.
4. Use **Stop control** at any time. No shell execution or silent installation is available to the desktop planner. It does not read passwords or upload screenshots.

### Mac permissions

Settings → Mac access has system-controlled Accessibility and Screen Recording access. Tauri `src-tauri/Info.plist` contains `NSAppleEventsUsageDescription` and `NSScreenCaptureUsageDescription`. If macOS prompts for additional access by `/usr/bin/osascript`, grant it only if you intend to use desktop automation. Permission changes can require restarting the app.

### Burp Suite Professional integration

- Java extension source: `integrations/burp/src/main/java/com/bughunter/BugHunterBurpBridge.java`.
- Build on Mac: `bash scripts/build_burp_bridge.sh` (requires JDK 21 and internet access to Maven Central).
- Install resulting `integrations/burp/build/BugHunterBurpBridge.jar` manually in Burp → Extensions → Installed → Add Java extension.
- Set the authorized test target in Burp scope. BugHunter reads a random per-extension local token from `~/.bughunter-claudex/burp-token` (0600 mode), authenticates to `127.0.0.1:18765`, and retrieves only metadata from in-scope Burp HTTP history (no bodies, headers, cookies or query strings). On the BugHunter side it also filters every returned URL to your authorized project scope.
- Ask Chat: `Show Burp proxy history for my authorized website project`.
- Settings → Optional tools → Burp → **Check actual Burp connection** checks the authenticated extension response, separately from port-listener detection.
- This read-only integration cannot trigger Burp's active scanner or modify intercepted traffic. Website audits remain controlled by the existing scope policy gateway.

### Cheat Engine

The existing native CE bridge, macOS memory bridge, and Chat-to-memory handoff are retained. The bridge is already a bounded multi-step agent with operator approval for attaching and writing memory. Cheat Engine itself and a compatible MCP server (especially under Wine) must be set up separately; this version does not pretend that the Mac native memory bridge is Cheat Engine.

## Validation performed in build environment

- Added Python tests for desktop-model action validation and Burp scope filtering.
- Python bytecode compilation, Java syntax compilation against minimal Montoya-compatible API stubs, TypeScript transpilation parse, and ZIP integrity checks.
- Could not build the Burp extension against the actual official Montoya JAR because the build container could not reach Maven Central.
- Could not compile/test Tauri Rust on macOS (build environment is Linux without Rust) or actually operate Burp/CE from here.

## Known limits and priorities

- UI automation relies on macOS Accessibility labels; some apps expose no usable buttons, and protected UI or unsupported Windows/Wine apps are not accessible through the native `.app` controller.
- Screen capture is local and manual, not consumed by the AI planning loop. This is not yet a vision-guided mouse agent.
- Unbounded autonomous computer control is neither implemented nor claimed. Native actions are capped at 40 per 15-minute session; the default UI loop proposes at most 14 steps.
- No autonomous exploit verification, game-cheat generation, Burp active scanning, or unrestricted system control is claimed.
- If you want to validate on your actual M1 Mac, install dependencies with `python3 scripts/setup.py` then use `npm run desktop:dev`. Before reporting a successful build, run `npm run build` and `cargo check --manifest-path src-tauri/Cargo.toml`.
