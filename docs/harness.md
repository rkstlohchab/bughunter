# Chat task harness

Chat now connects the existing OperatorAgent, app RPC services, dependency installer and Vercel agent-desktop. Inference uses the selected provider/model/effort; execution takes place on the desktop device.

Describe a computer or installation task, use a game setup request, or start a message with `/task`. The inline setup collects task permissions once: app/library access, computer control and screen sharing, local files, commands/adapters, and dependency installation. Follow-up questions and the final response stay in the saved conversation. Tool receipts open under **Tool activity**. **Task access** starts a newly scoped continuation; `/exit` ends the current task.

The initial permissions do not replace OS Accessibility, capture, device-debugging or administrator prompts. Computer access is broad GUI access, not an OS sandbox. The native memory panel retains its separate review controls.

## Connections

- `harness.start/get/send/stop/detach/status/features` manage saved `harness_session` records linked to `operator_run` and `chat`.
- Codex receives actual dynamic tools over its app-server connection, with host-side execution and per-call receipts. Claude and Gemini subscriptions use a JSON host RPC protocol; OpenAI-compatible APIs use function calls with the same host capability checks. Unavailable versions and quotas are errors; providers never switch silently.
- `app_features` discovers existing actions; `app_action` calls the validated app services. Source attachments and library calls retain project scope. Credential changes, task grants and approval controls are excluded from model-callable app actions.
- `system.apps` / `app_inventory` inspect installed-app catalogs on macOS, Windows and Linux. On macOS this includes wrapped iOS bundles. Results distinguish observed metadata from runtime verification.
- `desktop_tools` discovers the installed engine's exact MCP schemas. `desktop_call` uses a task-specific engine session; snapshot refs remain scoped by the engine. `desktop_describe` can inspect this task's captured image through the selected supported vision provider. Screen images stay in private artifacts and are omitted from text logs.
- Desktop tasks automatically draw a labeled cursor with the selected model, a unique task number and the target app, plus a click highlight. The engine session is created by the app, so the model cannot disable its own indicator. macOS and Windows actions stop if the indicator cannot render. On desktop sessions without a supported overlay, Chat reports the limitation. Completion, cancellation, Stop all and restart cleanup close the overlay.
- `adapter_tools` exposes existing adapter argument/mode documentation. A catalog entry is not verification of its dependencies, target compatibility or game offsets.
- The installer offers pinned `agent-desktop@1.3.0`, ADB, scrcpy, FFmpeg, Ollama and analysis dependencies where a package mapping exists. It uses npm/Homebrew on macOS, npm/winget on Windows, and npm/apt/dnf/pacman on Linux. Missing managers, privileges or distribution packages produce actionable errors. A successful package-manager exit is distinct from a found executable.

The desktop engine supports macOS 13+, Windows 10 1809+ and supported Linux desktops; actions vary by OS, display session and app accessibility. See the [upstream installation requirements](https://github.com/vercel-labs/agent-desktop/blob/main/docs/install.md). Windows npm shims are resolved to their Node entry points so model text is never interpolated into a command interpreter.

## Stop and recovery

**Stop all** latches execution off and advances the saved stop generation before cancellation. It stops operator runs, MCP calls, app-owned commands and installers, as well as existing media/source/task workers. **Resume tasks** enables new work; it never replays stopped actions or restores old task grants.

After a desktop restart, in-flight harness runs become **interrupted**. Receipts and messages remain available. A new task can continue from the saved conversation. Pending installations are reconciled as interrupted rather than claimed successful.

Video extraction uses its existing frame/audio/scene checkpoints. Refreshing the app does not discard completed course work. The knowledge library remains cited retrieval data; model-weight training is separate.

## Chat display

Each run produces one visible assistant reply. Intermediate narration and tool receipts stay in the activity history. Legacy completion echoes are repaired when a conversation opens; original execution evidence is retained. Markdown supports headings, emphasis, links, lists, tables, quotes, inline and fenced code, syntax highlighting, copy buttons, highlighted text (`==text==`) and math. Raw HTML is not executed.

Only the conversation scrolls. The composer and top bar remain visible. Right-side dashes jump between user prompts and support Up/Down keyboard navigation.

## Verification and rollback

Session evidence is in `artifacts/harness-wiring-2026-11-13/`. Mac runtime checks cover the actual Vercel MCP engine, Calculator actions and installed Among Us metadata, plus the Chat → selected ChatGPT model → app action → single saved reply path. The labeled pointer and its Stop all cleanup were verified directly through the same host tools without additional model inference; a subsequent model-driven indicator test was blocked by the account's usage limit. Windows/Linux install plans, CLI dispatch and recovery are tested with fixtures; live control on those OSes still needs a device running each OS.

Use the artifact folder's `rollback.py --check` to validate the retained baseline/diff, and `rollback.py --apply` to restore only this patch when file hashes still match. Rebuild the sidecar and desktop bundle after restoring. This preserves existing knowledge/checkpoints and does not reset other uncommitted work.
