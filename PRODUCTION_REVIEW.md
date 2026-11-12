# BugHunterClaudex — Production readiness review

Historical review scope: the uploaded `BugHunterClaudex-Agent-Activity-Update.zip`
and the production-readiness changes made at that time. See [validation](docs/validation.md) for subsequent checks.
This is a **production-readiness review**, not a completed нагрузочная-проверка or
certification. Static analysis is not evidence that a feature works on macOS,
Windows, Tor or an external network.

## Readiness judgment

**Development build; not production-ready.** The application can store sources,
create and run controlled investigations, chat with configured providers, and
launch two installed coding CLIs. Some actions are unverified on actual target
platforms, and full regression/build checks have not yet been completed for
this revision.

## Issues and required work, in priority order

1. **P0: Verify independent execution boundaries.** Local coding agents can
   execute commands and optional workspace writes. They are *not* confined by
   a system sandbox at the OS boundary. Tool-approval and permission models
   must be integration-tested, including privilege and subprocess inheritance.
2. **P0: Real platform E2E coverage.** Add clean macOS Apple Silicon and Windows
   builds with sidecar packaging, launched Tauri processes, local model request,
   Tor installation checks, and мост-ИП attach failure/recovery. A passing
   Python test does not confirm the desktop executable works.
3. **P0: Credential confidentiality.** The Python agent runner inherits most of
   the parent process environment. The tool launch path now removes common
   credential-named environment variables and tests that behavior. OS-level
   sandboxing, inherited file-system access, SSH agents and secret isolation
   still need independent review before accepting untrusted tasks.
4. **P1: Complete agent process supervision.** This patch adds a bounded stdout
   reader and deadline. Add long-running child-process tree tests on macOS and
   Windows, crash/restart reconciliation, and deterministic process reaping.
5. **P1: Data ownership and isolation.** Project identifiers are not strict
   tenancy barriers on all library and specialist entities. Verify which records
   each project can retrieve, including training data and Tor research.
6. **P1: Prompt-injection and export review.** Imported files and webpages may
   contain противостоящий instructions or credentials. They must stay untrusted;
   provenance, human reviews, redacted exports and independent tool approvals
   are essential. This patch adds a bounded taxonomy for ZIP imports.
7. **P1: Backup, restore and migrations.** Add schema-version migrations,
   encrypted backup/restore, restore smoke tests, and interrupted-import recovery.
8. **P1: Runtime health diagnostics.** Unify the model provider, bridge, Tor
   and external-tool readiness into one status monitor with actionable fixes.
9. **P2: Cross-platform UI regression.** Test keyboard navigation, dense forms,
   narrow screen layout, error overlays, streaming chats and long source names.
10. **P2: Model quality evaluation.** Use held-out tool-call tasks and human-
    reviewed prompt-testing examples. Retrieved knowledge is not model training.

## Changed in this pass

- Archive classification is accessible from AI Testing for ZIP files imported
  through Knowledge. It reports source locators and tentative labels without
  executing a prompt, calling a model, or marking test outcomes successful.
- The application seeds a versioned, high-level AI robustness reference into
  Knowledge on first desktop startup, deduplicated by content hash.
- Coding agent output reads are bounded, and a watchdog stops stalled jobs.
- Added backend/frontend CI checks (GitHub Actions) for supported platforms.

## Verification notes

- Isolated classifier tests and Python syntax checks: run locally.
- Full Python suite: unavailable in this container without optional backend
  dependencies. It is declared as a CI check, not represented as passed.
- Full Vite + TypeScript + desktop/Tauri build: not verified here.
- Live Claude/Codex CLIs, network integrations, and physical game process
  attachment: not tested.

## Handling an uploaded jailbreak-prompt ZIP

Open **Knowledge → Add**, upload the ZIP, and wait for indexing. Then open
**Workspaces → AI Testing → Import a prompt ZIP**, select the imported archive,
and choose **Analyze saved archive**. Review the categories and cited examples.
This does not execute prompts, fine-tune a model, or assert that any attempted
обход worked. Only explicit, verified experiment records should become
training labels.
