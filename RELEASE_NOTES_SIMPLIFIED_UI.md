# BugHunterClaudex — simplified Knowledge, Chat, Settings & local video

Updated source bundle based on `BugHunterClaudex-Mac-Qwen3VL-Whisper-Kaggle-KnowledgeGraph.zip`. This is a source distribution, not a tested/signed macOS application.

## Changes

- **Knowledge** opens to a plain file library: search, add files/links, inspect imports. Video analysis, relationships (formerly knowledge graph), saved methods, training examples, drafts and portable export remain behind **More knowledge tools**. No existing knowledge data is deleted.
- **Video AI**: "Sample one frame every N seconds" has a clear explanation. **Process entire video (no frame cap)** is the default for new settings and maps to `max_frames = 0`. For existing profiles, select this checkbox and click **Save video settings** to remove a previously saved limit. Positive caps remain optional (up to 1,000,000). Uncapped extraction walks the whole recording at the chosen interval and generates samples lazily instead of materializing all timestamps; processing still consumes time, memory and disk space. Time-keyed filenames prevent stale screenshots being reused across sample configurations. A video is not watched at full frame rate: gaps between sampled frames remain.
- **Local AI setup**: Install controls are aligned in responsive rows; installed components are shown as such. Component installation still requires explicit approval. `Recheck` updates detected state.
- **Chat**: Removed "What should AI do?" dropdown; the existing deterministic intent classifier routes messages to plain answers or task review. No task is executed from a document or instruction snippet. The redundant manual readiness check and already-ready confirmation/test button are gone; missing prerequisites appear only when they block the requested workflow.
- **Settings**: "Manage optional tools" expands and collapses a visible section; "Recheck now" refreshes live tool inventory, shows an error if it fails, and displays when the check ran. Advanced model options moved into optional tools; fewer default controls.
- **Burp Suite**: Removed the false-positive generic `burp` command check and the `mcp` Python installation proxy. A recognized PortSwigger macOS `.app` or `burpsuite` CLI is now required for installed status. Installed is not connected/verified; use the Burp integration to test connectivity.
- **Pause/Resume**: The global emergency stop is persisted deliberately; it was not a failed AI connection. Removed the top-bar "Resume requests" button. If prior work was paused, use **Settings → Task controls → Resume tasks**. Explicit approval policy, scope limits and stop enforcement remain active.
- **Custom AI instructions**: Settings explains that the configured provider receives your instructions and can retrieve cited excerpts from your own indexed knowledge and code. No custom prompt can guarantee that an external provider will ignore its restrictions. The workbench's local execution/approval controls are unchanged.

## Run on Mac

```sh
cd ~/Downloads
unzip -o BugHunterClaudex-Simplified-Mac-Video-AI-UI-Fixes.zip
cd bughunter
python3 scripts/setup.py
npm run desktop:dev
```

The app's SQLite data is stored separately under `~/Library/Application Support/BugHunterClaudex`; replacing source files should not remove the library. Back up your data before changing versions. Use `python3 scripts/package.py` to build locally once dependencies and the Apple build toolchain are installed.

## Verification performed in this build environment

- 13 focused backend tests passed, including uncapped 301-frame extraction and strict Burp inventory detection.
- All TypeScript/TSX source files parsed without syntax errors using the TypeScript compiler parser.
- Python bytecode compilation passed.
- ZIP integrity checked after packaging.
- Full backend suite was blocked by `keyring` missing in the container; a complete TypeScript type-check and Tauri/macOS app run could not be completed because the frontend installation and Apple toolchain were unavailable. Validate on your Mac before production use.
