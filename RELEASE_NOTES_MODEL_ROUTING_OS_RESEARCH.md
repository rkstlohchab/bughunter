# BugHunterClaudex — Model routing, integrated research, scoped computer controls

## Actual changes

1. **Chat model failover (opt-in).** Up to five explicitly saved backup configurations with separate system-keychain secrets. Chat tries a backup only when *both* Settings and the Chat toggle enable failover, and only after connection/timeout errors or HTTP 408, 429, 500, 502, 503, or 504. Authentication, malformed request, spending limit and permission errors never silently fail over. Settings revokes a backup credential if its endpoint or provider changes. The conversation (including selected knowledge references) will be sent to the backup service if failover occurs; therefore both switches default off.
2. **Web research in Chat.** A web search modal, an opt-in "Search web when replying" toggle, public Wikipedia keyword search without a key, and optional broader Brave Search API with a key saved in the system keychain. Selected search snippets can be saved as local, project-tagged Knowledge records. Automatically added references are tagged untrusted and URLs are supplied for citation. Search snippets are **not verified full-page evidence**.
3. **OS-aware sandbox for local research scripts.** Added a restricted, operator-approved Python workspace runner: Linux Bubblewrap namespace isolation when installed, and an *experimental* macOS sandbox-exec Seatbelt profile when available. Both intend to block network access and files outside the selected read-only workspace, limit CPU/runtime and output, and clear inherited secrets. Unavailable OS isolation fails closed; no unsandboxed fallback. Windows has no implemented secure runner.
4. **UI.** Settings includes backup models, web-search credentials and the Computer Workspace panel. Chat exposes Web search, optional live research and optional failover. Responsive spacing included.
5. **Tests.** Dedicated tests for route validation, opt-in failover, transient-only conditions, credential handling, web citation import, Windows fail-closed behavior and profile restrictions.

## What is **not** implemented

- **Not full PC control.** Arbitrary filesystem writes, screen input, operating-system privileges, remote desktop, and unlimited shell access are not exposed. macOS TCC (Accessibility, Screen Recording, Automation, Full Disk Access) requires user-managed permissions and a carefully scoped native implementation; do not bypass these protections.
- **Not full OS sandboxing for Claude Code and Codex.** Existing CLI agent tasks still rely on their own CLI controls, and this release **does not** put those CLI processes inside the new Python-workspace sandbox. This is a separate, security-critical production gap. macOS sandbox-exec is deprecated and is **experimental**, not a guarantee against all host access or escape vulnerabilities.
- **Not automatic model selection by benchmark.** Ordered, explicitly configured failover is implemented; benchmark scoring, semantic model selection, circuit breakers and rate-aware queuing still require work.
- **Not comprehensive autonomous internet access.** The new built-in search is operator-initiated or expressly toggled in Chat. Brave requires the user's independent API subscription/key. Research results are not silently executed or fed as trusted instructions.
- Native Apple-Silicon tests, a full npm build, macOS Tauri integration, notarization, accessibility testing and Windows integration are **not verified in this environment**.

## Mac setup

```bash
cd ~/Downloads/bughunter
python3 scripts/setup.py
python3 scripts/build_backend.py
backend/.venv/bin/python scripts/smoke_native_sandbox.py
npm run desktop:dev
```

The smoke script is optional and deliberately reports an error when sandbox-exec is unavailable. The app still works for Chat and existing workspaces without it.

To configure Kaggle backup: Settings → Backup AI models → Add backup model → custom OpenAI-compatible provider → `https://<your-server>/v1` → model name → Save backup models → Save the route API key. Enable fallback in Settings and choose **Allow backup AI** in each Chat where you consent to sending the conversation to that route. Paid models have individual budget/price confirmation fields; user-hosted models do not.

For web research: Chat → Web search works with Wikipedia immediately when its public API is reachable. To search the broader web, create your own Brave Search API key in the provider's dashboard and save it under Settings → Web search. Search results can be saved into project Knowledge; an imported snippet does not replace reading the full source. Chat → Search web when replying allows automatic searches for that turn.

The Computer Workspace panel requires a native sandbox and explicit confirmation for each code execution. This is intended for bounded workspace inspection, not for unreviewed AI-generated code or privileged computer automation.

## Production gates still open

- Signed native macOS runtime with TCC permission prompts and app capability grants.
- Tested isolation of **all** agents, including CLI plugins, system subprocesses, memory bridge and browser automation.
- Fine-grained app action permissions and human approval for writes, installs, system input, outgoing requests and process termination.
- Local desktop E2E tests, clean-machine installers, observability and crash recovery.
- Route health probes and circuit breakers; provider-specific retry budgets and request deduplication.
