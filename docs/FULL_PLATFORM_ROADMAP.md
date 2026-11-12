# FULL_PLATFORM_ROADMAP — forge + operator stack

Status as of 2026-11-12. This doc captures the architecture that lands with
the forge + operator stack (AGENTS.md §10), how it fits the 8 GB M1 daily
driver + Kaggle training workflow, and the remaining polish.

## Hardware model

- **Daily driver:** 8 GB M1 Mac. Serves small quantized models locally
  (Qwen2.5-Coder-3B or 7B Q4 via Ollama/MLX), runs the desktop app,
  assembles corpus. Does **not** train.
- **Training:** Kaggle notebook (T4×2 / P100, free tier), triggered from
  the local app via the Kaggle API. Trained LoRA adapter comes back as a
  Kaggle dataset version, pulled by ``kaggle.pull_adapter``, registered
  in ``AdapterRegistry``, activated for local serving.
- **Burst inference (optional):** ``remote_ollama`` provider already in
  the codebase targets a self-hosted Ollama in a Kaggle notebook — for
  the few cases where 8 GB local isn't enough.

## The loop

```
┌───────────────────────────── local (M1) ──────────────────────────────┐
│                                                                        │
│  past runs ──┐                                                         │
│  errors ─────┼─►  LearningForge  ─►  corpus bundle (JSONL)             │
│  repo ───────┤                                                         │
│  distill? ───┘                           │                             │
│                                          ▼                             │
│  ◄── AdapterRegistry ◄── kaggle.pull ─── Kaggle dataset (adapter zip)  │
│          │                                                            ▲│
│          ▼                                                            ││
│  LocalInference (ollama create / mlx_lm.server)                       ││
│          │                                                            ││
│          ▼                                                            ││
│  OperatorAgent  ──► OperatorTools ──► op_adapters/* ──► targets       ││
│       │                        │                                      ││
│       │                        ▼                                      ││
│       │                 Asset DB, Timeline, Vault                     ││
│       │                                                               ││
│       └─────────── transcripts into next forge pass ──────────────────┘│
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
                                                                         │
                                 ┌───────────── Kaggle (T4/P100) ──────┐ │
                                 │                                     │ │
                                 │  train_adapter.py                   │ │
                                 │    corpus (dataset) ─►              │ │
                                 │    QLoRA (unsloth/trl)              │ │
                                 │    optional DPO on preferences      │ │
                                 │    adapter zip ─► output dataset ───┼─┘
                                 │                                     │
                                 └─────────────────────────────────────┘
```

## Phases → files shipped

### Phase α — Learning Forge (daily-driver, no training)

- ``backend/bughunter/learning_forge.py`` — ``TraceMiner``,
  ``ErrorMiner``, ``RepoMiner``, ``Distiller``, orchestrator
- ``scripts/forge_corpus.py`` — CLI
- ``src/ForgePanel.tsx`` — UI panel

### Phase α' — Kaggle finetune bridge

- ``backend/bughunter/kaggle_finetune.py`` — push_corpus / launch_training /
  status / pull_adapter
- ``scripts/kaggle/train_adapter.py`` — the Kaggle-side trainer (Unsloth
  or TRL, 4-bit QLoRA, optional DPO)
- UI: Kaggle section of ``ForgePanel.tsx``

### Phase α'' — Adapter registry + local inference

- ``backend/bughunter/adapter_registry.py``
- ``backend/bughunter/local_inference.py`` — Ollama Modelfile builder,
  MLX-LM launcher
- Serving reads the active-adapter list; the existing ``provider: ollama``
  path picks up the custom model unchanged.

### Phase β — Operator agent

- ``backend/bughunter/operator_tools.py`` — unbounded tool surface
- ``backend/bughunter/operator_agent.py`` — run loop + approval queue
- ``backend/bughunter/provider_api.py`` — OpenAI-compatible helpers
- ``backend/bughunter/code_exec.py`` — compile+run sandbox
- ``src/OperatorMode.tsx`` — UI

### Phase γ — Adapters + asset DB + workflows

- ``backend/bughunter/op_adapters/*.py`` — nmap, httpx, ffuf, nuclei,
  hashcat, hydra, frida, radare2, ghidra, impacket, bloodhound, adb
- ``backend/bughunter/asset_db.py`` — host/service/credential/loot/relation
- ``backend/bughunter/workflow.py`` — DAG engine
- ``backend/bughunter/resources/workflows/{recon,web-deep,ad-lite}.json``
- ``src/AssetBrowser.tsx`` — UI

### Phase δ — Transport + vault + timeline

- ``backend/bughunter/transport/socks_chain.py`` — SOCKS chain manager
- ``backend/bughunter/transport/tcp_relay.py`` — raw TCP relay
- ``backend/bughunter/vault.py`` — Keychain-backed credentials
- ``backend/bughunter/timeline.py`` — unified event stream
- ``src/VaultPanel.tsx``, ``src/Timeline.tsx`` — UI

## The daily workflow (dj's path)

1. **Use the app.** Every run (chat, operator, study) persists a transcript.
   You don't do anything special; the data accumulates.
2. **When you want to teach the model** — Forge panel → pick what to
   include → Build bundle. ~minutes on an 8GB Mac. Nothing leaves the
   Mac yet.
3. **Push + train** — same panel → Push corpus → Launch training. Kaggle
   runs the notebook on T4×2. Poll status.
4. **Pull adapter** — Pull adapter when status is complete. It lands in
   the registry.
5. **Activate** — Tick the new adapter in the registry. Build Ollama
   custom model (one click). Local inference now uses base + your
   fine-tune.
6. **Use it, errors and successes accumulate, loop.**

## Recommended base models for 8 GB M1 serving

- **Qwen2.5-Coder-3B-Instruct** GGUF Q4_K_M (~2.5 GB). Fastest; tight
  tool-call quality.
- **Qwen2.5-Coder-7B-Instruct** GGUF Q4_K_M (~4.5 GB). Preferred —
  sharp enough for tool-use.
- **DeepSeek-Coder-V2-Lite-Instruct** Q4 (~9 GB) — marginal on 8 GB,
  swap heavy.
- For training on Kaggle T4: 7B is comfortable, 14B is doable with QLoRA
  + Unsloth. 32B needs A100.

## What's still open (good follow-ups, not blocking)

- **Continual trainer:** today a corpus push is manual. Add a
  ``continuous_mode`` setting that watches run counts and offers
  ``push_corpus`` when new data crosses a threshold. On by default
  with a 50-run trigger.
- **Procedural promotion:** reuse ``skills.propose`` on successful
  operator runs the same way the earlier autonomy increment did for
  the investigation agent.
- **Composition at serving:** Ollama supports a single ADAPTER slot
  today. For multi-adapter composition, either (a) merge-at-build
  (produce one adapter via weighted combination before load) or
  (b) switch to MLX-LM which supports a stack. ``local_inference.py``
  picks the first active; extend to merge if several are active.
- **DPO on interactive feedback.** When the operator rejects an
  approval or cancels a tool call, record that as a negative
  preference pair. The forge already has the shape (``ErrorMiner``);
  wire interactive rejections into it.
- **Workflow authoring UI.** Specs are JSON today; a simple node-link
  canvas for building them would be nice but not needed.
