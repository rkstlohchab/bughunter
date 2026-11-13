# Local Ollama connection

## Set up models in the application

Open **Settings → Video AI setup** to choose ChatGPT through Codex, Claude through Claude Code, or local frame analysis. Each subscription has a separate sharing-consent checkbox. The video provider is separate from the chat provider. Subscription analysis sends selected screenshots and nearby narration through the selected signed-in CLI; Whisper transcription runs on the Mac. Errors never switch the selected provider or use a paid API automatically.

The local catalog offers Qwen3-VL Abliterated 2B, 4B, and 8B weights (approximately 1.9, 3.3, and 6.1 GB). Select a vision model, open **Set up local AI tools**, approve the download, and choose **Download selected local model**. After installation, choose **Test selected frame model**. The catalog also lists installed models after inspecting their capabilities, so text-only weights are kept out of the vision selector. The 8B option may exceed practical memory on an 8 GB Mac; download size is not the runtime memory requirement. The app reserves 5 GiB of free disk space during downloads and shows progress and actionable failures.

Chat models have a separate selector. Use **Connect your AI → My own models** to select an installed text model. Local chat and video inference share a cancellable queue, and an app-managed Ollama server loads one model at a time. An independently running Ollama server retains its own configuration.

**Image connection verified** means the selected provider/model completed an image check. The built-in diagnostic must identify the objects in the image; a course-frame check preserves the response for inspection. Verification is saved per provider/model and local weights digest. This verifies the connection and sample, not factual accuracy across every tutorial. Inspect the recorded output before relying on a new model for detailed code or small text.

On this 8 GB M1 Mac, the reduced-memory profile uses one current screenshot, a 768-pixel overview, and a 4,096-token context; an unreadable-detail retry uses up to 1,600 pixels. Against the same Burp tutorial frame, the installed 2B model took 20.97 seconds but misidentified the browser as an IDE. The installed 4B model took 184.74 seconds and recognized more of the Burp settings, while still misreading the extension name and adjacent browser. These diagnostic outputs were not promoted to course knowledge. ChatGPT remains selected for the current extraction. The larger 8B download is optional and was not benchmarked here.

Open a recording in **Library** to review its timeline, narration, visual descriptions, extraction gaps, and controls. Completed audio and scene-scan windows are checkpointed, and completed observations become searchable during processing. **Stop All** cancels active work; resuming enables new tasks without automatically replaying cancelled imports. Reprocess the selected source to continue from its checkpoints.

If ChatGPT reports a usage limit, the source waits in the queue with an automatic retry time. Exhausted windows use the provider's structured reset timestamp. When that metadata is unavailable, retries back off from 15 minutes to a maximum of one hour. The worker remains free to prepare other sources locally. Queued waits survive an application restart; **Stop All**, source cancellation, and pause prevent them from starting. After signing in to another account, choose **Reprocess using saved settings** to retry sooner. Retry never changes the provider or selected model.

When the app starts with ChatGPT selected for video analysis, two imports can run concurrently. Each recording retains chronological frame context and its own checkpoints. Local-provider startup uses one import worker, and Whisper shares the local inference queue so it cannot overlap another local model call. Concurrent subscription work reduces elapsed time when the provider has capacity; it does not increase the subscription's usage allowance.

**Use Claude alongside ChatGPT** explicitly enables a second provider for concurrent imports. Choose a pinned model such as Claude Opus 4.7 and a start time; before that time both workers use ChatGPT. After it, the second worker uses Claude for new frames, at medium effort. Previously completed hosted observations retain their original model/provider provenance and are reused when the image and narration evidence still match. A failed Claude request waits for that provider; it does not change models or use an API key. Turning off parallel use affects future requests; it does not withdraw valid completed observations.

Rejected or superseded generated descriptions stay in the audit history and are excluded from active search and reusable procedures. Human corrections and cited notes are preserved. Course import builds a cited retrieval library; it does not train the selected model's weights.

## Existing OmniCoder text-model connection

The requested model is `zfujicute/OmniCoder-Qwen3.5-9B-Claude-4.6-Opus-Uncensored-v2-GGUF:latest`. It is a third-party Qwen model; the name does not make it Anthropic's Claude or a Claude subscription connection. The app's scope, exact-request approvals, and evidence requirements apply with every provider.

Run from the repository with the backend environment:

```sh
ollama pull zfujicute/OmniCoder-Qwen3.5-9B-Claude-4.6-Opus-Uncensored-v2-GGUF
backend/.venv/bin/python scripts/configure_local_model.py --prepare --check-only --smoke
backend/.venv/bin/python scripts/configure_local_model.py --model bughunter-omnicoder:9b
```

The original package failed the live native-tool check with a Jinja system-message parsing error on Ollama 0.32.15. `--prepare` creates `bughunter-omnicoder:9b`, reusing the same weights with Ollama's built-in `qwen3.5` renderer and parser. The original model remains installed. This fixes the model packaging; it does not change the app's permissions or mandatory instructions. The repaired model passed two native-tool inference turns in 274 seconds on this 8 GB M1 Mac. The check unloads between calls, so this includes model loading and is not a steady-state speed benchmark.

The check uses only `127.0.0.1:11434`, verifies the exact installed tag, rejects cloud-backed model metadata, and optionally performs two inference turns. Its only executable tool reads a temporary project's metadata; the final answer must include the random name returned by that tool. No website, device-control, or game-memory operation runs during this check. Passing it proves this small tool exchange, not the quality of a full investigation.

Configuration selects Ollama in the existing local app store, enables local-only mode, disables model fallback routes, paid fallback, and cloud transcription, and leaves API credentials in Keychain. Local embeddings are left disabled to avoid loading a second model on an 8 GB Mac; text search still works. The script writes a private settings backup to the app's `exports` directory and refuses to change models during active tasks. Factory defaults remain Claude Code for a new installation. Restart the app after using the configuration script.

The model download is approximately 5.6 GB. Its metadata advertises a text model with tool calling and thinking, not image understanding. Video frames can be retained in the library, but this model alone does not interpret those images. Transcription, OCR, and visual interpretation have their own dependency and capability checks. Local requests use an 8,192-token context, a 1,024-token output cap, thinking disabled, and a 240-second request timeout. These are bounded settings (`ollama_context_tokens`, `ollama_max_output_tokens`, `ollama_thinking`, `ollama_timeout_seconds`). The model's default context is 8,192 tokens; large courses are retrieved in passages, not loaded as entire videos into a prompt. On an 8 GB Mac, close other heavy applications and run one model at a time. A large context or long tool history can exceed practical memory and latency limits.

Importing a course stores extracted knowledge and citations in SQLite/source artifacts. Models retrieve that knowledge; importing does not modify their weights. Original files can be released after extraction is checked. This is not lossless compression: missed demonstrations, unsampled frames, and failed transcript sections cannot be reconstructed from a summary. Portable exports contain versioned JSONL data and reviewed training examples for future retrieval or training workflows; training another model remains a separate step.

References: [Ollama local settings and context options](https://docs.ollama.com/faq), [tool calling](https://docs.ollama.com/capabilities/tool-calling), and [the requested model's registry page](https://ollama.com/zfujicute/OmniCoder-Qwen3.5-9B-Claude-4.6-Opus-Uncensored-v2-GGUF).

Renderer reference: [Ollama Qwen 3.5 renderer, pinned to the installed version](https://github.com/ollama/ollama/blob/v0.32.15/model/renderers/qwen35.go).
