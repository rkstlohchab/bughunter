# BugHunterClaudex — Unified Mac-control awareness for AI models

## What was wrong

The standard Chat system prompt mentioned a tool-enabled desktop app but did not
spell out the computer-control action interface. Also, the Chat composer only
recognized a few hardcoded computer-control phrases. Selecting a Claude Code
subscription in Settings left ordinary Chat and the desktop planner blocked.

## Implemented

- `backend/bughunter/desktop_capabilities.py` contains one host-capability
  contract for every supported API/self-hosted model. It identifies the local
  macOS runtime even if inference is hosted on Kaggle or another server.
- The Chat backend appends that contract before the operator's custom prompt.
  It is included in primary and configured fallback model requests.
- The desktop planning backend reuses the same contract while still restricting
  the LLM to its fixed allowed UI-action vocabulary.
- The Chat composer now recognizes explicit Mac GUI action requests such as
  `Open TextEdit and type a message` and presents the existing **Control a Mac
  app** panel with the request prefilled. Questions remain conversations; Burp
  security audits and memory scanning continue using their specialist routers.
- Claude Code subscription inference is now supported through `claude -p` for
  text-only Chat and desktop action proposals, provided Claude Code is installed
  and authenticated with a Claude subscription. It runs from a temporary work
  directory and explicitly disables Claude's CLI tools (`--tools ''`). Tauri
  still owns all actual native computer actions.
- Tests cover prompt forwarding for Ollama, Kaggle-compatible, OpenAI and
  Anthropic API configurations, subscription invocation, Mac task proposals,
  and Chat routing.

## What this does not do

- It does not upload screen captures or local files to an AI automatically.
- It does not grant macOS Accessibility / Screen Recording permissions.
- It does not allow the hosted model to run shell commands or bypass the
  single-selected-app session rules. The operator must still select the Mac
  application and explicitly approve input and label sharing.
- It does not make unsupported applications or custom game UI accessible.
- A ChatGPT or Claude web subscription is not the same as an API credential.
  The subscription path implemented here is specifically the locally
  authenticated **Claude Code CLI**, not generic consumer web subscriptions.
- The actual macOS Tauri build and hardware access require verification on
  the user's Mac; only source-level checks were performed here.

## Mac setup

From the extracted `bughunter` folder:

```bash
bash setup-mac.sh --core-only
npm run desktop:build
```

To use Claude Code as the configured model, first run `claude auth status`
and sign in with the CLI if needed. For Ollama and Kaggle-compatible models,
continue using the existing Settings connection.

## Smoke test

1. Open Chat and type `Open TextEdit and type "Hello from BugHunter"`.
2. Confirm the control dialog appears with the objective prefilled.
3. Choose TextEdit.app, grant Accessibility through macOS, enable control
   label sharing and clicks/typing for this session, and start.
4. Observe real UI actions and inspect the target app before marking done.
5. Ask `Can you control my Mac?` in normal Chat. It should explain the
   approved local tool handoff without falsely claiming it already acted.

## Validation in the packaging environment

- 24 focused backend tests passed (an ephemeral test-only keyring stub was
  used because this environment lacks the `keyring` dependency).
- 21 TypeScript routing tests passed.
- Modified React and TypeScript sources passed syntax parsing.
- Python compilation and ZIP integrity checks passed.
- Native macOS/Tauri runtime tests **not performed**.
