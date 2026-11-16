# GameLab — operator-mode game cheat workbench

For the proposed game runtime work, use the [agent implementation plan](agent-implementation-plan.md)
and [phase manifest](phase-manifest.json). They define code ownership, research,
phase outcomes and acceptance checks; they do not assert that those capabilities
are already implemented or verified.

Current implementation and review instructions are in
[P04 review and handoff](P04-review-and-handoff.md). P04 connects Chat to durable
native operations and has macOS fixture evidence. Human review and independent
acceptance of the final P04 revision remain pending. The
[P00–P03 handoff](P00-P03-final-handoff.md) records the prerequisite history;
its instruction to start P04 is superseded by the P04 handoff.

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

## Progress log

### 2026-11-14 — P00 (Contracts and verification harness) → ready_for_review

Owner: role C (workflow synthesis). Reviewer: role V (adversarial
audit collective; identity distinct from owner). Implementation
revision: `8187f411ab50878cab555a88fcbe607af2cacc23`.

- **P00.contracts — pass.** Pydantic + TypeScript + Rust contracts
  share `CONTRACT_VERSION = 1`, snake_case wire format, and the
  `AddressRepr` pattern that distinguishes `null`, `placeholder` and
  `+0x0`. 45 pytest assertions, `npx tsc --noEmit` and
  `cargo check --locked` all green. JSON schemas and round-trip
  fixtures live under `contracts/game_runtime/`.
- **P00.fixture — blocked.** 11 fixture tests pass; 4 oracle-dependent
  tests skip on this macOS host because `task_for_pid` returns
  `KERN_FAILURE` without the `com.apple.security.cs.debugger`
  entitlement. Adversarial oracle audit items 1–4 confirm the oracle
  is structurally independent of the fixture's control channel (no
  import or call edge from `Oracle.read_*` into `Fixture.get`; a
  control-lie of 777 is visible on the control channel while the
  memory read sees the real 42). Audit item 5 fails: `oracle.py` has
  no secondary macOS read path (no `lldb`/`vmmap`/signed-debugserver
  fallback) when the entitlement is absent. Independence is preserved
  — tests skip rather than silently pass — but coverage is unmet.
- **P00.report_validator — pass.** 10/10 verifier tests green.
  Adversarial forge attacks across 8 forged-report variants
  (screenshot-only live-fixture evidence, sha256 mismatch,
  reviewer==owner, missing required check field, depends_on without
  the prerequisite run, `exit_code=1` with `result=pass`,
  `schema_version=999` tamper, and a known-good sanity report) all
  terminate with the expected exit code (1, 1, 1, 1, 2, 1, 1, 0).
  Three hardening notes recorded in `report.json` as follow-ups.
- **P00.baseline — pass.** `artifacts/game-verification/game-runtime-v1/P00/baseline/run.sh`
  captured the five-regression ground state of HEAD; aggregate
  `fail` recorded honestly because the backend pytest suite is
  pre-existing red on `8187f41` (pytest log kept under
  `logs/pytest.log`). `tests/test_baseline_report.py` validates the
  shape of the baseline report.
- **Phase status:** `ready_for_review`. `implementation_status`
  cannot promote to `verified` and P01/P02/P03 cannot start until
  the macOS oracle fallback (or codesigned test harness with
  `get-task-allow`) lands and `P00.fixture` flips to `pass`.
- **Run artifacts:** `artifacts/game-verification/game-runtime-v1/P00/run-001/`
  (`report.json`, `manifest.json`, `commands.jsonl`).
