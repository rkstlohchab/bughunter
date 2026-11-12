# Specialist training workspaces

BugHunterClaudex now has three domain-isolated curriculum types: **Burp Suite**, **Cheat Engine**, and **Tor research**. Each has independent projects, settings, instruction/response examples, source references, group IDs, reviews, and exports.

## What works today

- **Inference:** Existing Settings → Ollama supports models served by a local Ollama process. A Kaggle Models download is not itself an API. For an Ollama-compatible Kaggle GGUF, run `python -m pip install kagglehub`, then `python scripts/install_kaggle_gguf.py owner/model/framework/variation --name my-model`. Specify `--file relative/path.gguf` when the Kaggle variation includes multiple files. Choose `ollama` and the created model tag in Settings. Review the model license and resource needs before downloading.
- **Learning through retrieval:** Only examples that were *manually reviewed* can be returned by `training.search` with a domain filter. The investigation agent exposes `search_burp_training` (Burp only). The local-memory bridge retrieves reviewed Cheat Engine examples as bounded, untrusted context if automatic knowledge is enabled; no new memory tools are created by this retrieval.
- **Memory location task:** On the CE bridge's **Agent** tab, open **Guided memory location finder**, enter the owned offline process, data type and an observed value, then select **Prepare scan task** and **Send**. The agent uses the *connected bridge's* actual tool list to request first/next scans and verify candidates. A scan may need several rounds of changed/unchanged values. It does not predict a stable address across executions. Attaching and state changes remain approval-gated. macOS native tools include value scanning; Windows capabilities are determined by the connected Cheat Engine MCP server.
- **Learning from execution:** A completed bridge run can be saved as a **draft** example in an existing Cheat Engine curriculum. Review the trace for correctness and sensitive content, edit the desired response, and mark the example reviewed in Specialist training. Unreviewed traces do not enter exports or retrieval.
- **Tor research:** Reviewed public research captures can be promoted to draft examples with an operator-authored takeaway.
- **Fine-tuning preparation:** The **Export MLX dataset** action produces `train.jsonl`, `valid.jsonl`, `test.jsonl`, `manifest.json`, `provenance.jsonl`, and `README.txt` in a ZIP. Only reviewed instruction/desired-response pairs are included. Variants sharing a scenario group remain in the same split to avoid leakage. No cross-domain union is performed. This export does **not** alter model weights.

## Fine-tuning on Apple Silicon

The `scripts/train_mlx_adapter.py` command wraps `mlx_lm.lora` for explicit, local LoRA/QLoRA training. It expects an extracted training ZIP directory and an MLX-compatible model, **not** a GGUF-only Ollama file.

```sh
python3 -m pip install 'mlx-lm[train]'
python3 scripts/train_mlx_adapter.py --data /path/to/exported-dataset --model mlx-community/<compatible-model> --output ./adapters/ce-specialist --iters 200 --dry-run
python3 scripts/train_mlx_adapter.py --data /path/to/exported-dataset --model mlx-community/<compatible-model> --output ./adapters/ce-specialist --iters 200
```

The training utility refuses to launch outside Apple Silicon macOS unless `--dry-run` is used. Memory and disk must be checked before a real run. A small number of examples is not enough to demonstrate model competence; compare evaluations from the held-out `test.jsonl` against a base model before adopting any adapter. The script writes adapters separately; it does not automatically deploy them to the app. A converted/merged adapter must be served by a compatible runtime and selected explicitly.

## Model selection and limitations

“Uncensored” is a publisher label, not a compatibility, safety, or accuracy guarantee. Check model architecture, context length, chat template, quantization, license, RAM, and tool-calling ability. An ordinary local model can return plain text but may not reliably call tools; the app already uses a fallback protocol where native tool calls are unavailable. Hosted model policies and API behavior are outside the app's control; a system prompt changes requested behavior but cannot guarantee outputs.

Never treat raw process output, web pages or target-model responses as verified ideal completions. The reviewed dataset is a *snapshot* of authored instruction-response pairs; improving the underlying model requires a deliberate training run and evaluation. The existing approval controls on requests and process modifications are unchanged.

## Development checks

Run `PYTHONPATH=backend pytest -q backend/tests/test_training.py backend/tests/test_crossplatform_lock.py`. Full backend tests require the dependencies in `backend/pyproject.toml` (notably `keyring` and `sqlite-vec`) and may require OS toolchains. UI syntax checking is not a substitute for a completed TypeScript/Vite and desktop build.
