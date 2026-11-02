# BugHunterClaudex — One-task UI and source reliability update

## Daily workflow

1. **Settings:** Select the Kaggle/custom model and save/test its API key. All providers use the same shared user prompt. View missing optional integrations under “Tools available to your AI.”
2. **Knowledge:** Upload files, ZIPs, supported videos/audio, or public links. Wait for the source to show **Indexed/Ready**. The model is not fine-tuned; task/chat retrieval sends relevant excerpts to the selected model.
3. **Chat:** Ask a question or describe a task. Attach existing sources with **Knowledge** or upload from Chat. The task selector defaults to **Decide from my message**; explicitly select a task category if auto-routing is wrong. Tasks with tool side effects open a review dialog first.
4. **Tasks:** Follow recorded checkpoints, approvals, actual tool activity, errors, evidence and reports. Use **Advanced tools (optional)** for manual operation or setup of specific integrations.

Only four entries appear in primary navigation: Chat, Tasks, Knowledge, Settings.

## What was repaired

- React syntax bug in `RedTeam.tsx` carried forward from the preceding fixed release.
- Removed the seven competing buttons from the chat composer in favor of Upload, Knowledge, and one task selector.
- Added a pure, bounded task-intent classifier and explicit task-type override. Ordinary explanatory questions do not start a task.
- Task plans can pin up to six indexed source files; project-specific sources cannot be attached to another project's plan. The report references the pinned sources without executing their contents.
- Prevent stale conversations and knowledge attachments from surviving a project switch.
- Tasks view no longer queries an absent task ID if the active project changes, and opens the AI-testing and memory tools from recorded handoffs.
- Simplified navigation, clarified the assistant's internal prompt, and added responsive styling for compact task controls.
- Settings exposes an easily understood optional-tool inventory; advanced model routes, web search keys, and sandbox configuration are behind one disclosure.
- Added and revised focused tests for task routing, resource isolation, and the four primary sections.

## Execution boundaries — do not overstate these

- **Website review:** Automatic *read-only* page review only for a defined, approved target and functioning policy gateway. Cart changes, authenticated actions, purchases and active exploit sequences are not run unattended. The disposable cart lab remains available.
- **Memory:** Chat opens the native memory workspace. Permission to inspect a process and valid native bridge tools are still required. This is not an automatically verified pointer result.
- **AI prompt testing:** Chat creates a tracked evaluation plan, then the user selects the target model/test case in AI Testing. Trial results must be observed and reviewed; the app does not magically infer successful jailbreaks from filenames.
- **Research:** Existing document search and user-initiated web research remain available. Imported instructions cannot authorize tool calls.
- **Coding agents:** Delegation requires an explicit review and installed Claude Code/Codex CLIs; host-level agent sandboxing remains an outstanding platform requirement.

## Local checks executed in the build environment

- All project TypeScript/TSX files parsed without syntax errors using the TypeScript compiler parser.
- `backend/tests/test_orchestrator_and_skills.py` and `backend/tests/test_task_plan_attached_sources.py` passed (7 tests).
- Ten task-intent cases executed against the transpiled classifier passed; repeat with `npm run test:routing` after `npm ci`.
- No full desktop/Vite build or live macOS, Tor or memory integration was possible here: `npm ci` failed in the build environment. Run a full build on your Mac before treating this as production-ready.

## Installation

Back up your current app data using **Settings → Back up your work**, or make a separate copy first. Extract this archive into a new directory; do not copy its bundled research corpus into an already active data folder. Then:

```bash
cd ~/Downloads/bughunter
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run build
npm run desktop:dev
```

The `setup.py` command performs an `npm ci` and installs Playwright Chromium, which may take additional time. Do not interrupt it midway. Keep API keys outside source files.

## Scope

The specialist subsystems are preserved, not rewritten. The new default interface improves how they are discovered and invoked; it does **not** turn every historical workspace into a fully unattended autonomous agent.
