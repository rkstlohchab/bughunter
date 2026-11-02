# BugHunterClaudex — Conversation & Team UX update

Built on `BugHunterClaudex-UI-Polish-Dark`.

## Chat

- Replaced the old two-pane chat with a conversation-first interface: searchable recent chats, clear model/project context, friendly empty state, readable messages, mobile conversation drawer, keyboard-friendly composer and responsive attachment chips.
- Added **Attach** to import local files through the existing `sources.import` pipeline, including its document and media extractors (individual uploads <=64 MiB). Imports remain in the local library.
- Added **Knowledge** to select up to six existing indexed sources per chat; the backend retrieves bounded cited passages from their indexed text and includes them in the AI request, without treating document instructions as trusted commands.
- Existing chat references persist across follow-up messages. New chats start without attachments. Indexing or failed resources cannot be sent as finished documents.
- A cloud AI receives selected reference excerpts as part of inference. Importing does not train the model weights.
- Added a reviewable **Memory tools** handoff. Chat remains tool-free; selecting it opens the macOS memory or Cheat Engine workspace, where the bridge must separately connect to a process. No tool execution happens merely from a chat message.

## AI Team

- Replaced `More chat options` disclosure with two simple chat tabs: **AI Chat** and **AI Team (Advanced)**.
- Reworked optional AI Team into setup, tasks, and notes, with clearer descriptions of the local MCP coordination connection. It does not spawn Claude Code, Codex or memory tooling.

## Reliability and evidence

- Three additional Python regression tests cover referenced document persistence, pending-index failure and invalid source selection. Existing chat tests continue to pass.
- Three Playwright specs were added for the attachment picker, non-executing handoff and AI Team navigation. These specs are not claimed as executed on this machine.
- Source and TypeScript syntax validated with Python `compileall` and TypeScript transpilation. The full desktop build / browser test run is not verified because npm's local cache lacks required packages.

## Mac setup

From the unzipped `bughunter` folder:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

Back up the previous application data and extract into a fresh folder; do not overwrite a running application directory.
