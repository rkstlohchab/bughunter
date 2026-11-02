# Vercel agent-desktop in BugHunterClaudex

The Mac control dialog now offers two engines. The default is Vercel agent-desktop when it answers its CLI version command; otherwise use the built-in Swift helper. Both use the **AI model already selected in BugHunter Settings** (Ollama or any configured supported provider), and there are no additional API keys for this integration.

## Installation

Run `bash setup-mac.sh --core-only` to install/check the agent-desktop CLI and other build dependencies, then `npm run desktop:build`. Or install just the optional engine with `npm install -g agent-desktop@1.0.2`.

On the Mac, run `agent-desktop permissions --guide` if Accessibility access is missing. In BugHunter, open **Chat → Control a Mac app**, select an app, and choose **Vercel agent-desktop**. Use **Test app access** first; if it cannot read the target app, choose the built-in engine. Grant permission to BugHunterClaudex and any launcher shown in macOS Privacy & Security.

## What is integrated

- Reads the chosen app's accessibility snapshot using `agent-desktop snapshot --app <app> -i --compact`.
- Converts visible native control names to the existing BugHunter planner's `Button`, `Tab`, `Item`, `Menu item` and `Text field label` observations. Model inputs are limited by the existing privacy filter.
- Supports semantic `click` for unique named buttons/tabs/items and `focus` for named fields. When Vercel is selected, other existing actions (keys, typing, scrolling, menus) remain routed through the built-in helper. No model-supplied commands or executable paths are accepted.
- Re-snapshots before every ref action to avoid stale references; refuses multiple equally named controls; the native app-focus, session-expiry, approval and Stop checks remain in force.
- Reports agent-desktop's `delivery` status without inventing task-level verification. The UI still asks you to verify the finished task.

## Known limitations

This is a **source integration awaiting real macOS compilation and tests**. The CLI's observed JSON needs verification against your installed 1.0.2 release on your Mac. The Vercel engine needs accessible native UI controls and is not a replacement for Cheat Engine memory access, Burp's API, or game-rendered ESP. A minimally filtered self-hosted AI may produce action suggestions, but capability and reliability are not guaranteed. Tested mock parsing is not the same as physical Mac app control.

Official repo: https://github.com/vercel-labs/agent-desktop (Apache-2.0).
