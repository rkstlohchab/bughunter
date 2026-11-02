# AI Chat and Kaggle llama.cpp

**AI Chat** in the left sidebar is a direct conversation with the currently selected reasoning model. It does **not** run tools or require a target project. Conversations are saved locally in the workbench database. Use **Investigation** for project-scoped agent tool use, and the specialist workspaces for their respective tasks.

## Connect the Kaggle MoE model

Keep the Kaggle notebook, `llama-server`, and HTTPS tunnel running. In **Settings & tools → Reasoning provider**:

- Provider: **OpenAI-compatible endpoint** (because the Kaggle service is `llama-server`, not Ollama)
- Model: `bughunter-moe` (the server alias chosen with `--alias`)
- API base URL: the current HTTPS tunnel address ending in `/v1`
- Local-only inference: **off**, because Kaggle is remote
- API budget per run (USD): **0.00** when input and output price fields are both **0.00** for your own non-metered inference
- Pricing confirmed: **checked** after verifying your own hosting costs
- Store the `llama-server` API key using **Store credential**; it is not the ngrok authtoken

**Save settings**, then **Save & test connection**. The provider test for this endpoint is a configuration check. Open **AI Chat**, send a short message, and verify a real answer to test inference.

Do not publish an unauthenticated tunnel. The model API must require a key before you connect a public endpoint. If the Kaggle notebook ends, the model will be unavailable until you restart the notebook and tunnel. The URL may change.

## Budget input

The budget input now supports decimals (`step=0.01`) and zero (`min=0`). The backend already supports `0.00`. An **API budget of 0.00** is meaningful only when the provider's per-token price fields are also zero. A metered API with a positive price will be blocked at a zero budget. Kaggle GPU time quotas and third-party tunnel charges, if any, are separate from this per-token budget.

## Build this update on macOS

Extract the updated ZIP, then in the folder containing `package.json`:

```sh
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

The sidecar build creates `src-tauri/binaries/bughunter-backend-aarch64-apple-darwin` on Apple Silicon. Close any running older dev app first. Changes to the Python backend require rebuilding the sidecar; running the old executable will not provide `chat.send`.

## Tests

The new chat route was exercised with a simulated OpenAI-compatible response (three focused Python tests). TypeScript files were syntax-checked, but full Rust/Tauri compilation and live Kaggle inference were not verified in the packaging environment.
