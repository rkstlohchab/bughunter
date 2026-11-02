# BugHunterClaudex — Vercel Agent Desktop integration (2026-11-01)

Based on the previous **One-Command Mac Setup — AXBridge Fixed** release. All existing project features are retained.

## New

- Optional Vercel `agent-desktop` 1.0.2 installation in the one-command Mac setup (failure does not remove existing Swift helper).
- Mac control selection in **Chat → Control a Mac app**: Vercel or the built-in Mac engine. Uses the AI provider already set under Settings, including locally served models.
- New native Rust adapter translates accessible UI snapshots into the current desktop planner's controls, and performs exact-match, ref-based `click` and `focus` actions. It rejects ambiguous elements and consequential labels. Each ref action obtains a fresh snapshot.
- Unsupported Vercel actions continue to use the existing built-in Mac agent. App focus, explicit session approval, expiration, cancellation, and action receipts remain enforced.
- Settings → Mac access displays Vercel engine installation/health.
- Read-only verification: `bash scripts/verify_agent_desktop_on_mac.sh`.

## One-command Mac setup

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
npm run desktop:build
```

For an existing installation, install only Vercel's optional runtime:

```bash
npm install -g agent-desktop@1.0.2
agent-desktop version
agent-desktop permissions --guide
bash scripts/verify_agent_desktop_on_mac.sh
```

Open BugHunter, use **Chat → Control a Mac app**, and test using TextEdit first. Select the Vercel engine if available; otherwise select Built-in.

## Limitations and testing

- The integration is source code that **must still compile in the actual macOS Tauri environment**. No native macOS build or live UI operations were possible in this Linux container. TypeScript parsing, backend focused tests and ZIP integrity were checked here.
- `agent-desktop` is a GUI accessibility adapter, not an LLM and not a memory reader. It does not bypass macOS permissions, work in all visually rendered apps, or implement Cheat Engine memory scanning. It does not guarantee every game/desktop task.
- Default model may be Kaggle, Ollama/local, or another supported configured model. Minimally filtered local models can be configured through existing Settings, but their accuracy cannot be guaranteed. Screenshots remain local-only unless explicitly handled by another feature. Input text and UI label privacy boundaries still apply.
- 1.0.2 was the latest published release found at build time. For future versions, check the upstream CLI's release notes before updating because JSON envelopes may change.
- Official project: https://github.com/vercel-labs/agent-desktop (Apache-2.0).
