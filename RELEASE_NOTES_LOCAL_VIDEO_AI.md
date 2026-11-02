# BugHunterClaudex · Mac-local Video AI update

This release updates **BugHunterClaudex-Guided-Connections-Free-Web-Search.zip** without replacing its existing Chat, Tasks, projects, knowledge, Kaggle-compatible reasoning configuration, or approval gates.

## Included

- **Library → Video AI**: model readiness, start/stop of app-managed Ollama, opt-in component setup and progress, resource selection, transcription/vision timeline, video pause/resume/cancel, reprocess.
- **Local Qwen3-VL on Mac**: default `qwen3-vl:2b` for Apple Silicon; optional `4b` and `8b`. Frame analysis via Ollama's loopback `/api/chat` image interface. Screenshots are **not sent to Kaggle**. Already-installed Ollama models can be reused.
- **Local Whisper**: whisper.cpp `whisper-cli`, FFmpeg and a local `ggml-base.bin` checkpoint. Produces timestamped SRT utterance segments. A matching existing sidecar SRT/VTT is preferred; no external ASR upload.
- **Video import**: adaptive frame sampling, screenshot extraction, visual descriptions, transcript, timeline files and checkpointed visual analyses. A retry reuses visual analysis checkpoints and durable transcripts. Knowledge chunks preserve source ID, timestamp, modality, and local frame reference.
- **Knowledge graph**: timestamp-ordered `precedes` edges between indexed speech/vision passages, plus existing lexical technique and tool concepts, all linked to source.
- **Background ingestion**: existing serialized import worker runs the media pipeline. Model setup runs in a separate background thread; no repeated user Terminal command is required after prerequisites are installed.
- **Standalone images**: local Qwen3-VL descriptions are indexed in addition to optional OCR.

## Use on macOS / MacBook Air M1

1. Run the BugHunterClaudex desktop app **as you normally do**. This ZIP contains **source code**, not a prebuilt signed `.app`/DMG.
2. Open **Knowledge → My knowledge → Video AI**.
3. Click **Recheck**. If Ollama/Whisper/FFmpeg are already installed, reuse them; nothing is automatically downloaded.
4. Under **Install missing local components**, select the approval checkbox and click only the components you need. **Homebrew** needs to be installed once for automatic binary installation. For speech, choose **Install whisper.cpp** and **Download Whisper model**; for frames, choose **Download selected Qwen3-VL**. Install FFmpeg if missing. Installation and model download progress are displayed on the panel.
5. Choose **Qwen3-VL 2B** on an 8 GB Mac, set frame interval to 12 s, and save. Start local model if necessary. More frequent samples improve coverage at significant processing cost.
6. Import local videos by **path** for files above the 64 MiB file-upload cap, or upload smaller recordings. Open a source from **Video timeline** to inspect time-stamped segments. Reprocess older videos after enabling models.
7. Keep the existing **Kaggle/custom AI** reasoning connection as before under Settings. Indexed text can be sent to that model through the existing Chat retrieval system. Raw frames/audio remain local.

## Limitations and operational notes

- Local Qwen3-VL processes **sampled still frames**, not every video frame. It cannot reliably prove an individual click or exact click instant between samples. Frame timestamps identify sample times. Whisper SRT timings are approximate utterance boundaries, **not word-level or millisecond-perfect timestamps**. Do not represent inferred UI interactions as directly observed.
- Long recordings can take substantial CPU, RAM, disk space and time. Vision analysis runs sequentially to reduce M1 memory pressure. Pause/cancel takes effect **between** frame operations and stages, not in the middle of a running Whisper/FFmpeg subprocess.
- Existing source import and knowledge tables are preserved in the user's normal application data folder. The ZIP does not contain uploaded tutorials or model weight files.
- Local binary installation depends on Homebrew already being present. macOS may ask for normal permissions. Installation is **opt-in**, never silently triggered by content from an untrusted video. The model installer downloads from Ollama; the Whisper model downloads from the ggml-org/whisper.cpp Hugging Face repository. Install status is retained for UI polling until restart.
- Stop only terminates the Ollama child started by this app, not an independently running Ollama application. Local Ollama is bound to `127.0.0.1:11434` when app-managed; the client never sends raw frames to arbitrary network hosts.
- No claim is made that this distribution is a signed/build-tested macOS `.app`; desktop build must run on a Mac with its dependencies and Apple toolchain.
