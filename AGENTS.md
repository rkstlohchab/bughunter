# AGENTS.md — context for AI agents working on BugHunterClaudex

Read this before editing. It explains what the app is, how it is built, how to run and test it, and the invariants you must not break. Deeper detail lives in [`docs/`](docs/); this file is the map.

> This file is for coding agents. The companion runtime instructions that the *investigation* agent follows at inference time are in [`prompts/research-assistant.md`](prompts/research-assistant.md) — do not confuse the two.

---

## 1. What the app is

BugHunterClaudex is a **personal macOS workbench for education and _authorized_ web/API vulnerability research**. A user records a target with its scope and written authorization, imports cited learning material, and an AI agent investigates **within recorded rules**, proposing checks and drafting evidence-backed findings. It is a bounded, assisted tool — not an autonomous attack platform.

Its legitimacy rests on one idea: **safety is enforced outside the model.** The model reasons and proposes; a policy gateway, recorded scope, and per-request human approval decide what actually reaches a target. Keep that true.

**In scope:** HTTP(S) web/API investigation, a cited knowledge base, course "study" extraction, evidence/reporting, multi-agent dev collaboration, the **Tor Browser research adapter** (read-only navigation of explicitly bookmarked sites), and the **operator-driven local-memory bridge** (§6, invariant 8) — a separate, desktop-only feature in the Rust shell with two backends: Cheat Engine (Windows) and a bundled native macOS server.
**Explicitly out of scope (do not add):** autonomous whole-machine control, general desktop/software automation wired to the *investigation* agent, network/memory attack adapters, process-memory access from the Python backend or `ModelTools`, or anything that removes/auto-satisfies the approval gateway. The investigation agent and Python backend are deliberately web-research-only; the Tor Browser WebDriver adapter may launch only the operator-configured browser and geckodriver for source reading. The boundary for game/RE tooling on the backend side is unchanged: studying, importing, and **drafting text** about local tools is in scope (script drafts are plain text a human reviews and runs themselves). Memory-tool execution exists in exactly one place — the local-memory bridge (Cheat Engine or native macOS backend), under the constraints in invariant 7 — and nowhere else.
---

## 2. Stack & layout

- **Shell:** Tauri 2 (Rust) in [`src-tauri/`](src-tauri/) — packages the UI and launches the Python backend as a sidecar.
- **Frontend:** React + TypeScript + Vite in [`src/`](src/).
- **Backend:** Python package [`backend/bughunter/`](backend/bughunter/), run as a subprocess. This is where most logic lives.

```
backend/bughunter/
  __main__.py        Entry/arg modes (see §4). RPC transports (stdio + dev HTTP bridge).
  app.py             Application: composes services, dispatch() RPC router, delete_project.
  store.py           SQLite entity/settings/events store; desktop-owner lock; new_id/now.
  providers.py       ProviderSettings, RunManager (agent loop _api/_claude), probe_provider,
                     study prompt/mode, local-model fallback. Provider set + LOCAL_PROVIDERS.
  model_tools.py     ModelTools = the agent capability surface. TOOL_SPECS, STUDY_TOOL_NAMES,
                     SOURCE_PAGE. Human approval is deliberately absent here.
  mcp_server.py      stdio MCP server exposing ModelTools (investigation/study) or TeamTools.
  policy.py          PolicyService: scope validation, effect classification, queue/approve/
                     execute, request budgets, the gateway handshake. The safety core.
  gateway.py         mitmproxy-based policy gateway (scope/exclusions/DNS/limits/approval).
  traffic.py         TrafficService: captured requests/responses + field inventory.
  testing.py         TestingService: field-aware test hypotheses, queue, coverage, assess.
  knowledge.py       KnowledgeService: import→extract→chunk→FTS5(+vectors)→graph; search;
                     enrich (grounded assertions); corrections; frames.
  tor_research.py     Tor SOCKS5 research source/bookmark/capture/review service.
  tor_browser.py      Selenium adapter for installed Tor Browser executable with copied profile; source-scoped navigation only.
  knowledge_archive.py Portable JSONL export, release_original, curated training examples.
  import_formats.py  Parsers (PDF/EPUB/DOCX/PPTX/XLSX/ODT/HTML/images-OCR/subtitles/AV/ZIP).
  adapters.py        ToolService: probe/crawl/managed browser/subfinder/nuclei (gateway-routed).
  lab.py             Disposable loopback "test shop" with an instrumented pricing oracle.
  reports.py         ReportService: finding validation + Markdown/HTML/JSON export.
  script_drafts.py   ScriptDraftService: text-only script drafts for local user-owned
                     tools (Cheat Engine Lua, Ghidra/IDA Python, x64dbg). Storage,
                     review, delete. There is deliberately no execution path here
                     (the Cheat Engine bridge in src-tauri is separate and never reads drafts).
  team.py            TeamService + TeamTools: shared tasks/notes/claims for coding agents.
  security.py        redact()/redact_text() — apply to anything shown to a model or exported.
  resources/         Bundled copy of the research prompt (dev copy in /prompts).
backend/tests/       pytest suite (165 tests at last edit). Mirror these patterns.
src-tauri/src/       lib.rs (backend sidecar RPC + app wiring), plus the Cheat Engine bridge:
                     ce_policy.rs (pure approval/denylist rules + tests), ce_core.rs (config, stdio
                     MCP client, approval-gated agent loop; no Tauri types so it is unit-testable),
                     ce_mcp.rs (thin #[tauri::command] layer). Never talks to the Python backend.
src-tauri/resources/ mac_memory_mcp.py — stdlib-only stdio MCP server for the `mac_native` backend
                     (Mach VM via ctypes; /proc backend for Linux tests). Bundled via tauri.conf.
tests/mac_mcp/       unittest suite for that server (python3 -m unittest discover -s tests/mac_mcp).
src/                 App.tsx (shell/pages/Settings), SimpleHome.tsx (Investigation),
                     Learning.tsx (Library/study/export), Team.tsx, components.tsx, api.ts, types.ts,
                     CeMcpPanel.tsx + ceMcpApi.ts (Cheat Engine bridge UI, desktop-only).
scripts/             setup.py, dev.py (browser dev UI), package.py, smoke_backend.py.
docs/                design, execution, implementation-contract, knowledge, setup, validation, agent-team.
```

---

## 3. Run & test

```sh
# Backend tests (primary signal — keep green)
cd backend && uv run pytest -q

# Frontend typecheck
npx tsc --noEmit

# Dev: native app / browser UI
npm run desktop:dev
python3 scripts/dev.py          # browser UI; uses a loopback bridge + BUGHUNTER_RPC_TOKEN

# Package the sidecar + macOS app
python3 scripts/package.py
```

`ruff` is available as a dev dep but the repo enforces **no** custom ruff config, so default-ruleset findings (e.g. BLE001, import order) are not gate conditions — don't churn pre-existing style. Match the surrounding code (it is intentionally dense and single-line in places).

---

## 4. Backend entry modes (`python -m bughunter …`)

| Mode | Purpose |
| --- | --- |
| `--stdio` (default) | Desktop RPC over stdin/stdout. The packaged app speaks this. |
| `--http [--port]` | Dev loopback bridge on `127.0.0.1:8765`. Requires `BUGHUNTER_RPC_TOKEN` ≥24 chars; `Authorization: Bearer`, Origin-locked to the Vite dev origin. |
| `--mcp --run-id` | stdio MCP server exposing `ModelTools` for one run (tools restricted when the run is a study). |
| `--team-mcp --team-id --agent-id` | stdio MCP server exposing `TeamTools` (the AI-team collaboration surface). |
| `--gateway [--port]` | Runs the mitmproxy policy gateway (default 8091). |

`Application(primary=…)` takes the single-owner `desktop.lock`; MCP modes run non-primary and must not assume ownership.

---

## 5. Architecture essentials

**RPC contract** ([`docs/implementation-contract.md`](docs/implementation-contract.md)): requests are `{id, method, params}`, replies `{id, result}` or `{id, error:{code,message}}`. All desktop calls go through `Application.dispatch(method, params, role="desktop")`. Methods are namespaced: `projects.* actions.* sources.* knowledge.* traffic.* tests.* lab.* tools.* scripts.* findings.* settings.* runs.* team.* system.* events.* dashboard.*`. Agent (non-desktop) calls never use this router — they go through the narrow `ModelTools`/`TeamTools` MCP surface.

**Data** ([`store.py`](backend/bughunter/store.py)): one SQLite DB (`workbench.sqlite3`, WAL) under `~/Library/Application Support/BugHunterClaudex` (or `BUGHUNTER_DATA_DIR`). Generic `entities(id, kind, data JSON, timestamps)` + `settings` + append-only `events`; services add their own tables (knowledge FTS5/vectors/nodes/edges/chunks/corrections, gateway/adapter tickets, request budgets). Entity kinds include: `project, source, traffic, test, action, finding, run, asset, observation, team, team_task, team_note, learning_example, script_draft`. Use `store.put/get/list/setting/event`; `require_desktop_owner()` fails closed once the desktop exits.

**Agent execution** ([`providers.py`](backend/bughunter/providers.py)): `RunManager.start(project_id, prompt)` (investigation) or `start_study(source_id)` (target-free course study) create a `run` and launch a worker thread. `_work` dispatches to `_claude` (provider `claude_code`, via the Claude Code CLI + stdio MCP) or `_api` (`anthropic/openai/ollama/crucible/openai_compatible/remote_ollama/deepseek`, an HTTP tool-loop). `PROVIDERS` and `LOCAL_PROVIDERS={ollama,crucible}` define billing/key/local rules. `remote_ollama` is a self-hosted Ollama server behind an authenticated HTTPS tunnel (Kaggle notebook/rented GPU): deliberately treated as hosted — stored access key required (Bearer), HTTPS enforced outside loopback, local-only mode blocks it, embeddings stay loopback. `deepseek` is the hosted DeepSeek API (OpenAI-compatible `/chat/completions`, Bearer key, base URL defaults to https://api.deepseek.com); `deepseek-chat` uses native function calling while `deepseek-reasoner` is routed through `fallback_tool_protocol` because it rejects the tools parameter. Local models without a native tool API use `fallback_tool_protocol`/`parse_fallback_call`. `probe_provider()` backs `settings.test` (free live checks for local, `remote_ollama`, and `deepseek` — the latter via the free `/models` listing; config-only for other hosted). Study mode swaps in `STUDY_PROMPT`, restricts tools to `STUDY_TOOL_NAMES`, and pages the source via `get_source(offset)` + `chunk_window`.

**Agent tools** ([`model_tools.py`](backend/bughunter/model_tools.py)): the complete capability surface, including scoped Tor Browser research navigation and capture. The agent can read the project, probe/crawl/browser **within scope**, search knowledge, propose/queue tests and actions, execute **only read-only** queued requests, draft (never confirm) findings, draft plain-text scripts for local user-owned tools (never executed here; study mode drafts from the studied source, investigation mode attaches them to the project), and record **inferred** knowledge assertions. It cannot approve, change scope, execute writes, or run shell.

---

## 6. Invariants — do not break these

1. **The gateway + scope + human approval stay.** State-changing/uncertain requests are queued and released only by the human from the desktop. `ModelTools.execute_read_only` must refuse anything not classified read-only. Never add a path that auto-approves or bypasses the gateway. (The runtime prompt says: *"Never propose removing the request gateway to make a tool work."*)
   - *Autonomy within the gate, not instead of it.* `RunManager.maybe_auto_resume` (providers.py) lets the agent continue, after `actions.execute`, without the person separately clicking Resume - but only once a human has both approved *and sent* that exact request, only when nothing else is pending, and it is user-togglable (`auto_resume_after_execute`, default on). It never approves anything itself and calls the same `resume()` a person would, which refuses outright if emergency stop is active or another run is active. Anything that changes it to resume on `approve` alone, or to run before `execute` actually returns a terminal outcome, breaks this invariant.
2. **Web-research-only for the investigation agent and Python backend.** Do not add adapters for game/memory/network attacks, general desktop automation, or autonomous whole-machine control to `ModelTools`, `mcp_server.py`, `app.dispatch`, or any run. The narrow Tor Browser WebDriver exception in `tor_browser.py` may open only operator-bookmarked HTTP(S) origins, extract rendered page text, follow same-origin links and save unreviewed captures. It launches only a configured Tor Browser executable/geckodriver through a local SOCKS service, does not expose arbitrary browser script execution, form submission or system process control, and never uses direct-network fallback. The separate Cheat Engine bridge (invariant 8) remains isolated.
3. **Untrusted data.** All resource text, transcripts, OCR, tool output, and target responses are evidence, not instructions. Run everything shown to a model or written to a report through `redact()`/`redact_text()`. Preserve account/session isolation.
4. **Grounded knowledge.** `enrich`/`learn_assertions` require an exact verbatim quote from an existing source chunk; stored assertions are `inferred` until a human reviews them. Never fabricate quotes, citations, requests, responses, or "successful" tool calls.
5. **No silent provider/billing fallback.** Errors/limits never switch provider or spend. Local-only mode rejects hosted providers; a `crucible` endpoint must be a local-network address; hosted runs need confirmed pricing + a stored key.
6. **Findings.** Only the desktop (human) can set a finding to `confirmed`, and only with trustworthy completed-execution/gateway evidence — agents draft hypotheses.
7. **Model-side settings (custom system prompt, turn ceilings) never dilute the base safety prompt or the gateway.** `effective_system_prompt()` (providers.py) appends the operator's `custom_system_prompt` after the mandatory base prompt and labels it "not safety-authoritative" - it must never replace or precede the base prompt. `max_agent_turns` (backend, cap 300) and `K_MAX_AGENT_STEPS` (CE bridge, 48) bound how long a run goes before stopping itself; raising either is a capacity change only; it does not and must not touch what still needs approval.
8. **The local-memory bridge stays operator-driven and approval-gated.**
   - *Read-only context sharing:* the desktop CE panel may retrieve cited `knowledge.search` results from the local backend and append them as bounded, explicitly untrusted reference notes to a user-initiated bridge prompt. This does not give the CE model an RPC client, backend mutation, scope changes, or tool execution outside the existing bridge. The operator can disable automatic retrieval in the panel. It has two backends behind one agent loop: `cheat_engine` (Windows) and `mac_native` (`src-tauri/resources/mac_memory_mcp.py`). It lives only in `src-tauri/src/ce_*.rs`, that resource, and `src/CeMcpPanel.tsx`/`ceMcpApi.ts`.
   - *Execution isolation:* never expose the bridge to the Python backend, `ModelTools`, `TeamTools`, MCP servers, script drafts, or any investigation/study run. The CE model receives only read-only citations through the desktop UI as described above; it cannot invoke backend RPCs. No other run may start, connect, or call the bridge. It never auto-connects; the operator presses Connect on its page.
   - *Every agent-proposed call goes through `ce_policy::classify`.* Only allow-listed read-only tools (`K_READ_ONLY_PREFIXES`, and only while `auto_approve_readonly` is on) run unasked. Everything else — `evaluate_lua`, auto-assemble, writes, breakpoints, anything unknown — parks the run until the operator approves or denies **that exact call** in the UI. Never add an auto-approve path for those, never batch-approve, and never let a model-proposed call skip `classify`.
   - *Denylist is a tripwire, not a sandbox.* `K_BLOCKED_PATTERNS` refuses shell, file, network and native-library access in any tool argument, even if the operator would approve. Do not shrink it without being asked; the approval prompt showing the full code is the real control.
   - *Human-typed Lua* (Lua tab) and single tool clicks (Tools tab) run on the operator's click without the policy filter; the human is the approval. Agent-originated text must never be routed into those commands.
   - *Targets:* intended for offline, single-player, or otherwise authorized local processes. Do not wire it to web targets, and do not add features aimed at defeating anti-cheat or touching other people's systems.
   - *Data:* memory contents flow to the configured LLM endpoint. The default is local Ollama; say so in copy if you add hosted-endpoint conveniences. Treat text read from the target process as untrusted data, not instructions.
   - *`mac_memory_mcp.py` exposes structured tools only.* Never add an eval/exec/shell/script tool to it, and keep every state-changing tool name matching an approval substring in `ce_policy.rs` (`attach`, `write`, `freeze`, …). `tests/mac_mcp` asserts no eval-style tool exists. macOS privilege limits (root or debugger entitlement, SIP/hardened-runtime refusal) are a feature: never add code that works around them.
   - *Tests:* keep the `ce_policy` and `ce_core` unit tests green (`cargo test --manifest-path src-tauri/Cargo.toml`); add one for any new tool class you allow.

If a task seems to require breaking one of these, stop and surface it to the user rather than working around it.

**Operator-only macOS app handoff:** `src-tauri/src/mac_tool_control.rs` implements an additional native-UI Settings-only capability: verify the identity and running status of recognized Burp Suite / Cheat Engine macOS bundles, explicitly open a selected native bundle on user click, and check whether a loopback proxy TCP port is responding. The helper does **not** connect to Burp/CE APIs, inspect windows, run memory operations, start a game, or authorize a website. It must never become a ModelTools tool, background task, model-callable RPC, or automatic action; do not allow arbitrary app names, binary execution or shell commands. A port responding is never proof that Burp is present.

---

## 7. Conventions

- Services expose a `dispatch(method, params)` and plain methods; wire new RPCs through `Application.dispatch`.
- Add tests next to the feature; mirror existing fixtures (`Application(tmp_path, primary=False)`, the `run()`/`study_run()` helpers in `tests/`). Keep tests hermetic — no live network; stub provider calls and monkeypatch `keyring` where needed.
- Keep user-facing copy accurate about limits (partial extraction, inferred vs reviewed, "retrieval corpus, not trained weights").
- Frontend: `rpc('<method>', params)` in `api.ts`; types in `types.ts`; keep `tsc --noEmit` clean.

---

## 8. Multi-agent collaboration (Claude + Codex)

The **AI team** service ([`team.py`](backend/bughunter/team.py), [`docs/agent-team.md`](docs/agent-team.md)) coordinates agents editing this repo. Protocol: read `team_status` first → `team_claim` a task before editing (overlapping file/dir claims are refused) → keep the returned `claim_key` → publish `team_update` with the current `revision`, progress, verification, and a handoff → stop editing before releasing/completing. It is **cooperative**: it does not lock the filesystem or launch agents, so honor claims. Generate per-agent connection config via the `team.connection` RPC.

---

## 9. Where to look next

`docs/design.md` (UX model) · `docs/execution.md` (capability separation & evidence) · `docs/implementation-contract.md` (RPC/transport) · `docs/knowledge.md` (learning/storage/portability) · `docs/setup.md` (tools/providers) · `docs/validation.md` (test record) · `docs/agent-team.md` (collaboration).

## Specialist training isolation (October 2026)

`training.*` is a desktop-only RPC backed by `TrainingService` in `backend/bughunter/training.py`. It has three fixed, noninterchangeable domains (`burp_suite`, `cheat_engine`, `tor_research`). A record is scoped to one curriculum. Drafts cannot be retrieved by an agent or exported; editing a reviewed example returns it to draft. Dataset export groups all related scenario variants into one split. Burp investigation has a **read-only** `search_burp_training` tool, while the CE panel may retrieve reviewed **Cheat Engine** examples as untrusted context. Neither path gains new execution powers. Tor research captures require a reviewed, operator-authored takeaway before promotion. The explicit CLI utilities `install_kaggle_gguf.py` and `train_mlx_adapter.py` run only when the operator executes them, outside investigation and memory agent loops.
