# BugHunterClaudex — P0 Course Understanding & Procedural Knowledge

This is an incremental upgrade of the existing app; your saved database is not bundled in the ZIP.

## What P0 actually adds

1. **Resumable local course ZIPs:** Knowledge → Upload / add link → Large course / folder. Paste the absolute path of a ZIP on the Mac (for example, `/Users/yourname/Downloads/Game Hacking Course.zip`). This initiates a background course job. The ZIP must remain in place until its members have been queued. Course progress appears above your files. Individual lessons then index through the existing knowledge worker.
2. **Source-linked steps:** Open an indexed lesson → Steps extracted from this lesson. Candidate procedures contain only verbatim quotes, original passage IDs, the original locator, and video timestamp if available. Rebuild is available for an individual source. Nothing is automatically marked verified.
3. **Cross-course concepts:** New graph nodes for procedures, ordered steps, and recognized recurring patterns. Each relationship is linked to a source passage and labeled as observed order or lexical inference.
4. **Adaptive video sampling:** Settings → Video AI. Along with one frame every N seconds, an optional low-resolution scene-detection pass attempts to capture UI changes. Scene changes are not proof of clicks. The existing local Whisper transcription and Qwen3-VL frame description remain the multimodal processors. A failed/slow scene detection falls back to fixed-interval sampling. Frame analysis, audio transcription and local models must be installed/configured first.
5. **Portable export:** `knowledge.export` includes `candidate_procedures.jsonl` and `courses.jsonl`. Candidate procedures do **not** enter the fine-tuning training split automatically.

## Build and test on Apple Silicon macOS

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p0_learning.sh
npm run desktop:build
```

Launch the built app:

```bash
open src-tauri/target/release/bundle/macos/BugHunterClaudex.app
```

For large course ZIPs, choose **Large course / folder** in Knowledge; don't upload the entire ZIP through the 64-MiB browser upload form. Each supported member is extracted as a separate stored source in the app's data directory. Video processing may require significant time and storage.

## Status labels are meaningful

- `queued`: source is waiting for the worker; not yet analyzed.
- `ready` or `partial`: source has indexed text, possibly with warnings.
- `candidate`: an extracted sequence grounded in source quotations; **not a verified technique**.
- `training_eligible=false`: raw procedural claims do not constitute reviewed fine-tuning examples.

## Limits and unfinished work

- Local ZIP archives: 16 GiB compressed, 12 GiB total declared expansion, 12,000 entries, 4 GiB per member; invalid paths, symlinks, excessive compression, and encrypted entries are rejected or skipped. Nested ZIPs are not treated as executable lesson content. No archive member or downloaded code is executed.
- Media extraction is local and checkpointed; success depends on macOS having FFmpeg, whisper.cpp, the speech model, Ollama, and a supported Qwen3-VL model. Scene detection uses a low-resolution, time-limited FFmpeg pass, so brief interactions may be missed.
- Extracted action descriptions are **not** guaranteed click-by-click transcriptions. A screenshot cannot establish that a specific click occurred. Source descriptions are grounded to sampled timestamps, and procedures remain candidates until verified separately.
- The ZIP does **not** contain the original 444 MB course files or already-processed transcripts. Importing those is an operation for your Mac.
- The full native macOS/Tauri build is not executed in the Linux development environment. Run the verification script and provide logs for platform-specific failures.
