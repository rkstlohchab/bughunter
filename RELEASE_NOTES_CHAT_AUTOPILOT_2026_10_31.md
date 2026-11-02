# BugHunterClaudex — Chat-first automation upgrade

Date: 2026-10-31  
Build: Source code for macOS/Tauri (NOT a verified production `.app`)

## What has actually been implemented

- **Autopilot backend** (`backend/bughunter/autopilot.py`): deterministic request classification, preflight validation, recorded runs, and explicit provenance. An LLM response cannot authorize a tool or expand a website's scope.
- **Chat integration**: file inspection, knowledge searches, and controlled model evaluations can start from normal requests. Results are persisted as conversation messages, linked to an actual Task record. A clear link opens the Task history.
- **Imported game/program inspection**: recognizes PE EXE/DLL, ELF, Mach-O, ZIP, APK, and Java archives; computes the actual full-file SHA-256 digest; records bounded archive metadata without executing its contents. Does not claim an ESP or money hack was discovered from file metadata.
- **Website automation**: delegates authorized, read-only website reviews to the existing gateway-controlled auditor. Still requires scope, safe GET paths, explicit approval, and an established gateway connection; no unrestricted third-party scanning.
- **AI robustness evaluation**: performs one neutral BLUE/RED instruction-priority test with the configured supported model using the existing RedTeam service; stores its actual response and an *untested* verdict until reviewed.
- **Knowledge retrieval**: provides exact source and passage identifiers rather than fabricated conclusions.
- **Reviewed training-data export API**: `autopilot.export_training` returns JSONL examples sourced from manually reviewed model trials only; it does not export unreviewed trials, API keys, or model weights. Existing Knowledge and Training panels retain their export interfaces.
- **Optional tool installation in Settings**: approved, allowlisted Homebrew installation of FFmpeg, Ollama, whisper.cpp, JADX, Apktool, or Ghidra; background status with success/failure. Burp Professional and Cheat Engine remain user-installed/licensed tools. This UI does not run arbitrary shell commands.
- **Memory task reliability**: fixes a missing local configuration reference in the Cheat Engine handoff; preserves explicit approval for memory access and mutation.
- **Binary attachment readiness**: original local APK/EXE files can be inspected without having to wait for a natural-language document index.

## End-to-end user flow

1. In **Settings**, choose your reasoning model. Use **Install tools** for optional free dependencies if needed and approve each installation.
2. In **Chat**, upload an owned `.exe`, APK, or other supported file and ask: `Analyze this game executable`. The file's real metadata and digest appear in Chat; the task is recorded.
3. Or select an authorized web project (with scope, authorization, safe GET paths), start the built-in gateway in Settings, and ask Chat to `Audit my website`. Confirm authorization, then review the Task and its evidence.
4. Or ask Chat to `Test prompt injection on my configured AI`. A neutral model-resilience trial runs, records the response, and remains ungraded until reviewed in the AI Testing workspace.
5. To prepare data for later model training, use the existing Training/Knowledge export workflows or the new `autopilot.export_training` RPC after explicitly reviewing tests.

## Limitations that remain

- **Not** a universally autonomous operator of every app, APK, Windows game, Cheat Engine instance or Burp Suite Professional extension. Native macOS automation, Windows compatibility, paid tool licensing, active process selection, and permissioned memory tasks depend on the actual Mac.
- File metadata alone **cannot prove or produce** a working ESP, memory modification, or unlimited-money patch. These require authorized offline dynamic analysis, supported target architecture, and verification.
- The website flow is limited to authorized, bounded, read-only HTTP checks and can only label heuristic findings as hypotheses until validated.
- The model test is one simple controlled single-turn evaluation, not a general automated jailbreak finder. Provider policy and connection requirements remain applicable.
- The Homebrew installer is Apple/macOS-specific; live installation and GUI permission behavior require testing on an actual Mac. Supported package availability may change.
- Local data retained by the previous installation remains separate from this source ZIP; **back up your existing app data before replacing versions**.

## Validation performed in this build environment

- 8 focused Python tests passing (including a previous false-positive Burp detection test).
- Python compilation successful for the complete backend package.
- Source-level TS/TSX syntactic diagnostics: zero errors for all frontend files using the TypeScript compiler's transpile pass.
- Archive structure and ZIP integrity checked after packaging.

Not performed: full npm + Tauri build, Apple Silicon GUI permission test, provider-call test, actual Homebrew installation, Burp Pro live traffic integration, or game-memory dynamic analysis. This build environment cannot install the project's complete npm dependencies or run a macOS Tauri desktop session.

## Mac setup

Unzip the project and run from the `bughunter/` directory:

```bash
python3 scripts/setup.py
npm run desktop:dev
```

`npm run desktop:dev` starts the development app via Tauri; `python3 scripts/dev.py` starts the local backend separately for backend-only debugging.

The new system does *not* automatically take whole-computer control. macOS Accessibility and Screen Recording permissions, selected-app access, and sensitive actions still require user approval.
