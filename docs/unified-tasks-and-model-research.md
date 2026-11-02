# Unified tasks and model-specific prompt research

## Tasks

Chat delegates **explicitly requested** local Claude Code/Codex runs through the task ledger. Web investigations, knowledge studies and bounded Tor captures also create task records; older records appear without copying their data. The Agents screen shows the real underlying job status, available command events, errors and approvals. Task retries create a new job; file-writing agents are never replayed silently. Tor collection supports a cooperative stop at page boundaries; it cannot retract a page request already transmitted. Memory analysis remains a handoff to the local workspace until there is a tool result to record, not an imaginary completed scan.

Approval for requests changing external state remains with the existing gateway. Neither the task ledger nor the prompt corpus supplies new privileges.

## Model-specific archive

The user-supplied `abusegpt.zip` is bundled under `backend/bughunter/assets/prompt_corpus/` together with `index.json` and one reviewed-as-descriptive (not efficacy-reviewed) Knowledge summary per **model family**. The file is copied into the frozen Python sidecar by `scripts/build_backend.py`.

- **AI Testing → Model-specific prompt research**: Filter by model family, technique, and filename; read original text as untrusted evidence; inspect nearby screenshots; assign or remove screenshot associations and write reviewed observations.
- **Knowledge Library**: The model-specific summaries are indexed as separate Markdown documents on first launch. They contain descriptions and archive paths, not active jailbreak instructions. Search them by model and technique.
- **Raw prompt files**: Retained byte-for-byte inside the bundled archive, including nested ZIPs. Only bounded `.txt`, `.md`, `.markdown` files are extracted for *read-only indexing*. Embedded executables, `.py`, `.sh`, `.env`, and other attachments are **not** executed, imported as commands, or automatically read into Chat.
- **Screenshots**: Associated with the nearest preceding document in the same archive folder; where there is no preceding document, the next document is suggested. All associations are explicitly marked as inferred and can be corrected before review. Screenshots do not prove that a prompt succeeded.
- **Duplicates**: Raw files are retained with their SHA-256 and an exact-duplicate link, not silently dropped.
- **Model attribution**: Folder/filename names provide *claimed model family*, including multiple claimed models for a single file. The original filename remains visible for more specific apparent variants or versions. A label does not prove that the prompt was tested on that model.
- **Review**: Human-reviewed outcomes are stored separately in persistent `prompt_corpus_review` records. An outcome labeled observed_success means a human reported that observation; it is not independently validated. No imported sample automatically becomes a fine-tuning training label.

Research assets and the active Knowledge Library are **separate storage layers**. A model can use the descriptive Knowledge summaries for context, but raw adversarial prompts are not injected into system instructions or sent to a remote provider automatically.

## Important production blockers

- No live Tauri macOS/Windows end-to-end build was run in this environment.
- No live Claude Code, Codex, Tor, Burp, or memory-bridge call was run in this review.
- The task ledger does not yet unify every interactive browser click, memory-bridge event, and approval-resolution event. Memory bridge interactions need a structured, per-task event adapter for true single-screen traceability.
- A bundled archive stores potentially sensitive content; review data and access permissions before sharing a compiled app or source archive with third parties.
- AI model-specific benchmark claims require repeatable evaluation results, model version/date, and evidence. They cannot be inferred from filenames or screenshots alone.
