# GameLab — operator-mode game cheat workbench

Parallel surface to the Investigation agent. Lives entirely in the
forge/operator stack (per AGENTS.md §10 invariants — no cross-plumbing
to the investigation agent). The investigation agent's `ModelTools` is
untouched.

## What's in it

- **Knowledge base** — `prompts/game-knowledge/` — anticheat teardowns,
  bypass stacks, ESP architectures, behavioral + account opsec.
  Imported into the FTS5 retrieval store by `scripts/install_gamelab_knowledge.py`.
- **AI guide** — `prompts/game-cheat-ops.md` — runtime prompt the GameLab
  operator agent loads when answering `gamelab.ask`.
- **Backend service** — `backend/bughunter/game_lab.py` — RPC namespace
  `gamelab.*`.
- **Op adapters** — `backend/bughunter/op_adapters/{vision_esp,dma_memread,
  arduino_hid,kmbox,bgmi_mobile,game_offsets,hwid_spoof}.py` — the
  actual hardware/software touch points, registered into OperatorTools.
- **Reference cheats** — `cheats/external-vision/`, `cheats/mobile-bgmi/`,
  `cheats/dma-stack/`, `cheats/kernel-mapper/`, `cheats/hypervisor/` —
  standalone runnable code + firmware.
- **Frontend** — `src/GameLab.tsx` — panel UI with hardware auto-detect,
  project creation, per-stack recipe start/stop, AI guide chat.

## Flow for a new project

1. Open GameLab → **New project** → pick game.
2. The service runs `gamelab.hardware_check` and `gamelab.recommend_stack`;
   the UI shows which stacks are ready vs missing hardware.
3. Pick a stack + create project.
4. From the project view, start a recipe. Each recipe routes through
   OperatorTools → the corresponding op_adapter, which spawns the
   long-running pipeline as a subprocess.
5. Ask the AI guide in-panel — it pulls from the knowledge base, scopes
   advice to this project's game + stack + hardware.
6. Behavioral + account opsec run alongside via `gamelab.opsec_check` —
   feed in your stats to get flagged floors.

## Setup runbooks

- [`setup-external-vision.md`](setup-external-vision.md) — capture card + YOLO + Arduino
- [`setup-dma.md`](setup-dma.md) — PCILeech + MemProcFS + KMBox
- [`setup-bgmi.md`](setup-bgmi.md) — scrcpy + YOLO + optional root
- [`setup-kernel-mapper.md`](setup-kernel-mapper.md) — BYOVD + manual map
- [`setup-hypervisor.md`](setup-hypervisor.md) — Type-1 HV skeleton

## Running the knowledge-base indexer

After first clone and after any edit to `prompts/game-knowledge/`:

```bash
cd backend && uv run python3 ../scripts/install_gamelab_knowledge.py
```

The script chunks every markdown file, writes `source_group=game-knowledge`
on each chunk (so `gamelab.knowledge_search` can filter), and bumps the
FTS5 revision.

## Isolation from the investigation path

Per AGENTS.md §10.4 invariant 3, the GameLab service:
- Does NOT import `ModelTools`, `PolicyService`, or `RunManager`.
- Does NOT route through the mitmproxy policy gateway.
- Lives behind the operator-mode opt-in: `operator.enable` must be
  on for `gamelab.start_recipe` to work.

The investigation agent's §6 invariants are intact.

## Autonomy mode

Per AGENTS.md §11, autonomous mode is ON by default. GameLab recipes
start without per-call approval. The operator can flip it off on a
per-adapter basis via the operator panel's approval settings.
