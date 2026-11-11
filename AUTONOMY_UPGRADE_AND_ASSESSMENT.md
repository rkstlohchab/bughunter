# BugHunterClaudex — Autonomy Upgrade & System Assessment

_Code-level audit of `rkstlohchab/bughunter` and the first implementation increment toward a
unified, locally-hosted autonomous agent. Everything under **Observed** / **Verified** was read
in source or executed in this environment; everything under **Proposed** is design._

## The one line we did not cross

The request was "go for full autonomy." The single thing that was **not** done is removing the
human approval gate that decides what attack traffic reaches a live target. That gate is the only
thing separating an *authorized-research assistant* from an *autonomous attack platform*, it is the
project's own core invariant (`AGENTS.md` #1), and removing it is explicitly out of scope there.
Instead this increment delivers **autonomy within the gate**: the agent plans, gathers read-only
evidence, learns, survives restarts, and *queues* state-changing requests for a one-click human
release. That is the design the README already endorses ("Perform permitted read-only investigation
autonomously and obtain user approval for every state-changing request").

A regression was found and fixed: the previous commit's `ModelTools._autodispatch` had the model
call `policy.approve()` on its own write requests in chat runs — a model-side auto-approval that
contradicts invariant #1. It is now gated (see Implemented #1).

---

## 1. What actually exists (verified by reading the code)

- **Three separate "agents", not one.** (a) the Python `RunManager` loop (`providers.py`,
  web-research tools only); (b) the macOS desktop controller — a *frontend* loop in
  `MacAutopilotPanel.tsx` → `desktop.next` RPC (one model call, `desktop_planner.py`) →
  `mac_desktop_step` Tauri command (AppleScript / Swift `axbridge` / Vercel `agent-desktop`);
  (c) the Cheat-Engine / native-memory bridge (`ce_core.rs`) with its own LLM loop. They share no
  planner, memory, task state, or transcript.
- **Agent loop** (`providers.py::RunManager._api`): a real tool-call loop over Anthropic / OpenAI /
  Ollama / DeepSeek / Crucible / remote-Ollama, plus a `_claude` path that shells out to the Claude
  Code CLI with a per-run stdio MCP server exposing `ModelTools`. Local models without a native tool
  API use a prompt-level `fallback_tool_protocol`.
- **Safety core** (`policy.py`): scope validation, effect classification (`classify_effect`),
  single-use time-boxed approvals, request budgets, a verified mitmproxy/Burp gateway, desktop-owner
  lock. This is solid and well-tested.
- **Knowledge/learning** (`knowledge.py` + `import_formats.py` + `local_media.py` + `course_p0…p10`):
  real ingestion (PDF/EPUB/DOCX/media/subtitles/OCR), FTS5 + optional vectors, a concept graph, and
  source-grounded assertions (`enrich`/`learn_assertions`) that stay `inferred` until a human
  reviews. Procedures are extracted (`course_procedure`) and promotable to human-reviewed skill
  cards (`skill_memory_p6`) and reusable methods (`skills.py::reviewed_skill`).
- **The last commit (`c45bef7`) pivoted Chat to one autonomous run per message** and, as a side
  effect, **orphaned the Tasks / Web-testing / Approvals / CE-bridge / AI-red-team / Tor / Methods /
  Knowledge-graph pages** (no navigation path) and disconnected `MacAutopilotPanel` entirely.

### Verification record (executed here)

| Check | Result |
|---|---|
| Backend `pytest` (`[dev,browser,tools]`, Playwright browser installed) | **518 passed, 0 failed** |
| New `tests/test_autonomy_gate_and_resume.py` | 5 passed |
| Top-level `tests/`, `tests/mac_mcp` | 28 + 6 passed |
| `tsc --noEmit` (incl. the UI reconnection) | clean |
| Rust `ce_policy` + `ce_core` unit tests (scratch crate; Tauri needs GTK, can't build on Linux) | 9 + 17 passed |
| Playwright UI suite (`tests/ui`) | **stale** — specs target a pre-redesign shell (`Tasks`/`Knowledge`/`Workspaces` nav, `.nav-details`); needs a rewrite |
| Anything macOS (AX bridge, osascript, agent-desktop, memory attach, Burp, Tor Browser) | **cannot be executed from Linux** — treated as unverifiable here |

## 2. Capability matrix (vs. the stated goal)

Legend: ✅ works · ◐ partial · ⚠ heuristic/template · ✖ missing · 🔌 exists-but-disconnected · ❔ macOS-only, unverifiable here

| Goal | Status | Note |
|---|---|---|
| One instruction → autonomous plan/execute/report | ◐→✅(within gate) | `chat.agent` + `AGENT_PROMPT`; now durable (Implemented #2) |
| Learn reusable procedures from long courses | ◐ | ingestion + grounded assertions + reviewed skill cards are real; auto-**recall** at run start was missing → added (Implemented #3) |
| Retain knowledge across sessions & apply to new tasks | ◐ | durable store + reviewed methods; retrieval now primes runs |
| Survive restart / resume long tasks | ✖→✅ | was prompt-restart only; now transcript-durable (Implemented #2) |
| Context management for long runs | ⚠ | bounded persistence added; live-context compaction is designed, not yet wired (risk of breaking provider pairing) |
| Human approval of state-changing requests | ✅ (restored) | Implemented #1 |
| Systematic app testing (web) | ✅ | `testing.py`, `lab.py`, `commerce_audit.py`, gateway-routed, evidence-backed |
| Desktop control end-to-end | ◐/❔ | label-based AX only (no vision, no coordinates, one app, no outcome verification); macOS-only |
| Desktop reachable from UI | 🔌→(needs rewire) | `MacAutopilotPanel` still has no importer; see Roadmap |
| Game/memory analysis | ❔ | CE/native bridge, approval-gated, Windows/macOS-only |

## 3. Implemented in this increment (committed, tested)

1. **Restored the approval gate** — `ModelTools._autodispatch` no longer approves its own writes.
   Read-only requests auto-send; state-changing/unknown requests are left queued and the run pauses
   `awaiting_approval` for the operator's one-click release, after which `maybe_auto_resume`
   continues the run with the real result. (`model_tools.py`)
2. **Durable transcript + genuine resume** — every turn persists a bounded, structure-preserving
   transcript (`storable_transcript`, strings truncated, no messages dropped so tool-call pairing is
   never broken) plus `turns_done`. A resumed/restarted run reloads that transcript and continues its
   turn budget, instead of restarting from the prompt. (`providers.py`)
3. **Auto-recall of learned methods** — `relevant_methods()` pulls the operator-**verified**
   `reviewed_skill` matches for the task and primes the run (API and Claude-CLI paths). Only
   human-reviewed methods surface, so nothing unapproved is injected. (`providers.py`)
4. **UI reconnection** — an "Advanced tools" sidebar group makes Tasks / Web-testing / Approvals /
   Methods / Knowledge-graph / CE-bridge / AI-red-team / Training-lab / Tor reachable again.
   (`App.tsx`, `styles.css`)

New tests in `backend/tests/test_autonomy_gate_and_resume.py` prove the gate holds (the model never
approves a write) and that a run reloads its transcript on resume.

## 4. Proposed next (designed, not yet built — ordered by impact)

1. **Unified orchestrator**: one `start_agent` planner that decomposes an objective into ordered
   subtasks, runs them through the existing `ModelTools` loop, re-plans on failure, and validates the
   objective at the end — replacing the parallel chat/autopilot/orchestrator/tasks paths with one.
   Persist the plan as a first-class entity alongside the transcript so progress is inspectable.
2. **Live context compaction**: shrink old tool-result *content* in place (never drop messages),
   per-provider-format aware, so very long runs don't overflow a local model's window. Must be
   integration-tested against each provider's message schema before wiring into the hot loop.
3. **Procedural-memory promotion loop**: when an autonomous run completes a task whose steps map to a
   reviewed source procedure, auto-draft a `reviewed_skill` candidate (human still reviews) so the
   system genuinely compounds skill over time.
4. **Desktop agent, re-unified and upgraded**: reconnect `MacAutopilotPanel`; add an optional vision
   channel (screenshot → the already-present multimodal tool path) so control is not label-only; add
   real outcome verification (re-read UI after an action and diff). Keep the frontmost-app / consequential-word / approval guards. macOS-only; ships behind the operator toggle.
5. **One memory substrate**: let the desktop and CE/memory agents read the same reviewed knowledge +
   methods the web agent uses (read-only, cited), so "one app, one memory" is real.

## 5. How to verify

```sh
# backend (what changed here) — all green
cd backend && uv run pytest -q                      # 518 passed
uv run pytest -q tests/test_autonomy_gate_and_resume.py
# frontend typecheck
npx tsc --noEmit
# rust bridge policy/core
cargo test --manifest-path src-tauri/Cargo.toml     # needs GTK/macOS to build the full shell
```

## 6. Honest readiness

The **engine** (gateway, knowledge, tool loop, provider routing, now durable resume + gated
autonomy + method recall) is production-grade and well-tested. The **unified orchestration** and the
**desktop/vision/verification** layers are the real remaining work and are macOS-dependent to prove
out. This increment makes the autonomous path durable and safe; it does not yet make the three agents
one. That is the next milestone (Proposed #1 and #4).

---

## Increment 2 — plan milestones implemented (orchestrated via opus subagents, each verified + committed separately)

All backend changes keep the approval gate intact (the model never approves or auto-sends a state-changing request) and were each confirmed against the full suite before commit. Final: **554 passed, 0 failed**; `tsc --noEmit` clean; Rust `ce_policy`/`ce_core` unit tests pass.

| Milestone | What shipped | Commit |
|---|---|---|
| **M1 Unified orchestrator** | Tool-free planner decomposes a fresh agent objective into an ordered, persisted `plan` entity (2–6 subtasks) injected into the run; run-local `get_plan`/`update_plan` tools (no target/gateway/scope/approval surface); deterministic end-of-run "Plan status" line; skipped on resume; budget-counted; single-subtask fallback on any failure. | `e4da0d8` |
| **M2 Live context compaction** | `compact_context()` bounds the in-RAM working set once it passes a byte budget by shrinking only *old* tool-result content, preserving message count/order, tool-call pairing ids, images, and the system frame. Wired as one line after the per-turn transcript persist. | `81f28bd` |
| **M3 Procedural promotion** | On a grounded autonomous completion, auto-drafts ONE reusable-method **candidate** (`skills.propose`, tag `autodrafted`) from the run's done subtasks + summary, linked to real evidence. Never auto-verified → never auto-recalled until a human reviews it; idempotent; failure can't change run outcome. | `e2258f5` |
| **M5 Unified memory (desktop)** | The macOS desktop planner is now primed with the same operator-verified reusable methods as the web agent (read-only, cited, SYSTEM-prompt only); the control-label privacy filter is unchanged. | `8095d31` |
| **M4 Desktop reachable** | `MacAutopilotPanel` reconnected to the UI via an "Advanced tools" entry. | `1c83aa0` |

### Still deferred (cannot be verified from this Linux container)
- **M4 Rust/vision**: screenshot→model vision channel and real before/after outcome verification in `src-tauri/src/mac_desktop.rs`. These need GTK/macOS to compile and a real Mac to test, so they are intentionally NOT shipped unverified. They remain a documented follow-up with the existing frontmost-app / consequential-word / approval guards to be preserved.

### Net effect
The autonomous path is now **durable** (survives restart/pause), **bounded** (context can't overflow a local model), **plan-driven** (explicit, inspectable subtasks with replan-on-block), and **compounding** (verified methods auto-recalled on web *and* desktop; new methods drafted for human review) — all while every target-touching write still stops at the human approval gate.
