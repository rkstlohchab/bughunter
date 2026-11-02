# BugHunterClaudex — Continued Autopilot Implementation

Date: 2026-10-31
Distribution: updated cross-platform **source project**, not a compiled or macOS-verified `.app`.

## Real changes made in this iteration

1. **Deeper passive EXE/DLL inspection:** `autopilot.py` reads bounded Windows PE machine type, sections, NX/ASLR/CFG capability bits, and imported library names where the header is safely parseable. No code from the uploaded binary is executed. These bits are *header indicators*, not evidence that a target is exploitable.
2. **Deeper APK inspection:** safely enumerates Android DEX files, native library architectures, APK signature-file indicators, and Android manifest permissions/settings only when the manifest is ordinary readable XML. Android binary AXML is explicitly labeled undecoded instead of guessed. Includes a bounded inventory of explicitly exported components if available.
3. **Evidence-linked review leads:** reflects observable hardening gaps such as a declared debug build, cleartext-traffic allowance, executable-and-writable PE sections, or missing PE header mitigation bits. Every lead says how it needs to be verified. None is called a confirmed vulnerability, working game exploit, ESP, or modification.
4. **Live Chat task progress:** a status panel follows the existing recorded `work_task` for chat-launched website reviews, showing pages/requests/leads, the latest event, completion or failure, and an explicit stop action. The panel never invents completion. Other completed autopilot tasks also display their recorded evidence count.
5. **Evidence memory and export:** completed, actual file inspections are retrievable by filename, digest, or format via research queries and are exportable as project-scoped JSONL through `autopilot.export_evidence`. The normal `Knowledge -> Export portable knowledge` ZIP now includes `observed_file_evidence.jsonl`. Neither export silently promotes raw material into verified training labels.
6. **Safer automatic intent routing:** studying/learning about prompt injection does not automatically start billable AI evaluation; an explicit test/evaluate/run request is required. Unattached conceptual game learning is treated as research or chat, rather than asking for an executable file.

## Validation completed here

- Seven focused hermetic Python tests passed, covering synthetic PE metadata, APK-readable manifest evidence, undecoded binary AXML, project-scope isolation for exports, incomplete/missing originals, evidence provenance in the knowledge ZIP, and conservative review leads.
- Python backend compiles with `python -m compileall`.
- 27 TypeScript and TSX source files passed TypeScript `transpileModule` syntax diagnostics.
- The ZIP was generated from the updated source and is checked for corruption after packaging.

## Explicitly not verified/completed

- **No full Tauri application build on macOS**: this environment is Linux and lacks the complete npm dependency tree and macOS build environment.
- **No automatic control of arbitrary games or Cheat Engine/Burp Suite Professional**: those require installed compatible applications, deliberate target selection, credentials/licensing, OS permissions, dedicated integration testing, and approved scopes.
- **No confirmed game hacks or live-site vulnerabilities.** Static PE/APK findings are observations, not exploits. The existing website review remains gateway-controlled, scoped and read-only.
- **No general autonomous desktop access** and no bypassing macOS permissions, app sandboxing, or provider safety policies.
- **No weight updates / neural-network training.** Structured evidence and human-approved datasets may be exported for separate training.

## Install from this ZIP

Keep your current application data backed up. Extract this new ZIP to its own folder, then run from its `bughunter/` directory:

```bash
python3 scripts/setup.py
npm run desktop:dev
```

Tool installs live in Settings and ask for explicit approval. A missing external commercial tool is not silently installed. `Chat -> View recorded task and evidence` opens the underlying Task record, and a website audit launched through Chat now displays its actual progress in Chat.
