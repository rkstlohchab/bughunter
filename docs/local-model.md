# Local Ollama connection

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
