# BugHunterClaudex — Chat-led agent execution and task activity

## Interface

- **Chat** is where the user requests agents: “Deploy Claude Code and Codex to review my project.” A small confirmation panel selects the agents and optional local working directory. The ordinary configured LLM is not required to launch these local coding CLIs.
- **Agents** is now a separate first-class navigation destination, not an “AI Team Advanced” configuration page. It lists actual saved tasks, current states, per-agent states, command/tool events, errors and completion statuses. Polling refreshes the view while the app is open.
- The former shared-repository MCP coordination backend is preserved for existing users, but no longer exposed as a prerequisite for launching an agent task.
- Tool readiness is available in Workspaces → **Check installed tools**. It detects common local installation paths and supplies setup guidance. This check does not itself connect to the tools.
- The memory bridge button now reads **Connect memory** instead of “Connect MCP”. MCP means *Model Context Protocol*: an interface for tool communication; the button starts a local tool connection, not an actual process attachment.

## Execution contract

- The runtime launches the installed `claude` and/or `codex` CLIs, not a simulated model.
- An explicit chat request (or the Agents button in Chat) opens a small review panel; clicking **Deploy agents** starts the selected clients.
- Agent runs default to **read-only**: `codex exec --sandbox read-only --json` and `claude -p --permission-mode plan`. You can explicitly enable **Allow local file edits** for a selected local working folder. That uses Codex workspace-write sandbox or Claude Code acceptEdits mode. With two selected agents in edit mode, the first edits and the second independently reviews read-only rather than writing concurrently. The commands are fixed argument arrays, not shell-interpolated user prompts.
- Records are stored in the existing SQLite entity store (`agent_job`, `agent_job_event`). The task captures reported CLI events, including commands, tool results and errors where the CLI emits them. It cannot reconstruct activity that the CLI does not report.
- A user can **Stop agents** from the Agents tab; system-wide Stop also requests termination. If the desktop restarts with an active job, its historical state is marked `interrupted`, not assumed completed.
- Missing CLI installations or an invalid specified local directory cause a clear error rather than a fabricated started task. Launching requires an authenticated, functioning Claude Code/Codex installation in the desktop app's PATH.

## Limits and unfinished items

- This is *real local CLI task execution and monitoring*, not automatic unrestricted control of Burp, Tor, memory tools or the operating system. Existing specialist-workspace actions and approval flows remain separate.
- Two read-only CLIs can run concurrently. In edit mode, one CLI edits and the other reviews afterward, preventing simultaneous writes. There is not yet an automatic result-synthesis or merge phase; edits remain inside the user-selected folder. The CLIs retain their native sandbox and permission behavior.
- Projects can be selected in the app header; an optional working directory tells the coding CLIs which source tree to examine. A name-only project has no source checkout to inspect until a folder is selected.
- No external CLI was actually launched in this environment; subprocess simulation validated the event flow. The full desktop build and end-to-end live CLI testing must be run on macOS.
- UI tests and npm build could not run in this environment because required frontend packages were unavailable. TypeScript/TSX syntax parsing and Python compilation succeeded.

## macOS development

From the extracted `bughunter` folder:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

Install and sign in to Claude Code and/or Codex CLI separately. Check **Agents** for detected availability. To request collaboration, open Chat and type `Deploy Claude Code and Codex to review my project`, then supply the local project folder in the prompt dialog and click **Deploy agents**.
