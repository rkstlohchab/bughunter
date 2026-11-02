# BugHunterClaudex — specialist curricula / local model training revision

## Implemented

1. A new top-level **Specialist training** UI with three independent learning domains: Burp Suite, Cheat Engine, and AI red-team experiments. Each domain can contain multiple projects, each with a distinct system prompt and model provenance field.
2. `TrainingService` on the desktop RPC (`training.*`) persists project-scoped, tagged instruction/desired-response pairs and their source and scenario group. Only manually **reviewed** examples may be retrieved or exported. Editing resets the review state; duplicate pairs within a project are rejected. Deletion cascades within the selected workspace only.
3. Portable, group-isolated MLX-LM training ZIP exports (`train.jsonl`, `valid.jsonl`, `test.jsonl`, `manifest.json`, `provenance.jsonl`) with source references and deterministic, scenario-group-based splits. An export is a dataset, **not** a model training run. Training examples are not pooled across domains.
4. A user-run KaggleHub-to-Ollama import script for **compatible GGUF models**. Explicitly validates model handles and Ollama names, chooses an unambiguous GGUF (or requires `--file`), and uses argument-array subprocess calls.
5. A user-run MLX-LM LoRA/QLoRA training utility for **Apple Silicon**, with export checks, CLI dry-run and adapter output path. This requires separately installed MLX-LM and compatible weights. It does not automatically load the resulting adapter into Ollama or the app.
6. The Burp investigation agent gains one read-only `search_burp_training` tool that returns reviewed Burp examples only. The CE panel can retrieve reviewed Cheat Engine examples alongside existing knowledge notes; no additional execution routes are exposed.
7. The CE bridge gets a **Guided memory location finder** to prepare a tool-aware scan/refinement task for an owned offline process, plus the ability to save a completed bridge trace as a **draft** example in a CE curriculum. It is not automatically labelled correct or trained.
8. Reviewed AI red-team trial records can be promoted as **draft critique/analysis examples**, with the reviewed verdict and lesson as the desired output. Their observed target-model outputs remain untrusted context.
9. Fixed a concrete Windows-startup blocker: unconditional `fcntl` imports in the Python store/application locking path are replaced by a cross-platform nonblocking file lock. Linux owner-lock regression tests pass; Windows code is untested on Windows.

## Verification

- New backend feature/script/cross-platform-lock tests: passed.
- Full backend suite with an in-memory keyring stub: **211 passed, 6 skipped, 2 deselected**. The two excluded knowledge/embedding tests require the optional `sqlite-vec` setup; they remain failing without it. With `keyring` absent, the original unmodified test collection also fails until that dependency is installed.
- Bundled native macOS memory-server Python suite: **6 passed** in its Linux test backend.
- Frontend source syntax: **all `.ts`/`.tsx` files parsed without syntax errors** using global TypeScript.
- Full `tsc`/Vite build, Playwright tests, Rust/Tauri build, live model APIs, real CE Windows bridge, actual macOS debugging and MLX model training: **not run**. `npm ci --offline` could not install dependencies (`yallist` was not cached); Rust toolchain and native desktop environments are not present here.

## Remaining known limitations

- The Windows CE bridge still needs the separately installed Cheat Engine MCP server configured in the CE panel; that server is not bundled. The provided macOS native server is bundled, and a Linux backend is present for its tests. Availability of first/next scan on Windows depends on the external CE MCP server's exposed tools.
- CE bridge configuration may contain plaintext API keys and CE tokens. OS credential-store migration and a live desktop build are pending.
- Reviewed examples are retrieval context until a separate LoRA training run is explicitly performed. A model labelled “uncensored” is not guaranteed to understand tools, satisfy a task, or comply with an external provider's policy.
- The original request/state-change approval gates remain. Custom prompts configure model behavior but do not override application authorization/approval logic.
- Kaggle weights, MLX dependencies, native GUI builds, external model inference, and full-model fine-tuning are not bundled in this archive. Hardware limits and model licenses still apply.

See `docs/specialist-training.md` for workflows.
