# AI connections and model selection

Settings offers Subscriptions, Paid APIs and My own models. Every provider uses a model dropdown. **Refresh models** reads the selected provider's catalog without sending an inference request. An optional **Enter a custom model…** choice supports servers without a model-list endpoint. Saved models remain visible when a provider cannot be reached. Each saved connection retains its own endpoint, selected model and price confirmation.

Chat's model picker uses the same three categories. Hover or click a category to see its providers and models. Keyboard: Up/Down between categories, Right or Enter to open, Up/Down between models, Left to return, Escape to close. Tools, Refresh and Stop all have visible labels in the top bar.

## ChatGPT subscription

The `codex` provider uses the installed Codex app-server and its existing ChatGPT sign-in. Tested with `codex-cli 0.166.0`. Sign in once with `codex login`, then refresh models. The app reads account type and model metadata; it does not extract or store OAuth credentials. API-key sign-ins are rejected for this provider, and failed subscription requests never fall back to API billing.

Official transport reference: [Codex app-server](https://learn.chatgpt.com/docs/app-server). Account status uses `account/read`; the picker uses `model/list`. Conversations use ephemeral threads and app-supplied tools. Inherited shell, network search, plugins, apps, hooks and other MCP connections are disabled; app tool calls retain the existing scope and approval checks. Stop all cancels the active transport, and closing it terminates its subprocess. A live reply and one synthetic app-tool round trip passed on this machine. Plan availability and usage limits remain provider-controlled.

Claude subscription uses its existing Claude Code connection and the `sonnet`, `opus`, and `haiku` aliases. API providers use authenticated model-list endpoints; Ollama lists installed models. Discovery does not confirm that every listed model supports every app workflow.

## Course library check (12 November 2026)

The Game Hacking Course project has 15 ready imported sources from the Downloads course folder, containing 387 stored passages. Its 11 videos include 209 sampled frames. The remaining `.exe` source has no text passages and is explicitly marked unsupported; accompanying C++ source is indexed. All course files were represented in the source list, and a scoped keyword search returned passages from the videos.

These are searchable references, not retrained model weights. Semantic embeddings are disabled for these sources; keyword retrieval works. Select **Game Hacking Course** in the project picker to include its references in Chat. Video frames are samples and do not prove that every on-screen action was captured.
