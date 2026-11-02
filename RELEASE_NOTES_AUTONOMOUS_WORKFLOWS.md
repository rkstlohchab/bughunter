# BugHunterClaudex — Task plans, reviewed methods and simplified navigation

**Status:** Development source release. The backend has been tested in this environment; the native macOS Tauri UI, Tor integration, memory bridge, and signed installer have **not** been verified here.

## What actually works

1. **Chat → Plan task → Tasks:** Describe work in Chat, choose Research / Code review / Authorized web investigation / AI evaluation, then review the resulting task in **Agents** (Tasks & agents).
2. **Durable plan checkpoints:** The planner runs only passive local dependency detection, project-scoped knowledge retrieval, and lookup of previously verified methods by itself. Results and source IDs are stored in the shared task ledger and survive a restart. An active external task is interrupted on restart and is **not automatically replayed**.
3. **Approval boundary:** Coding-agent launch and web investigations stop for an explicit click. Coding agents launched through the planner run in **read-only** mode with the pre-existing CLI guards. Web investigations require a project with a scope and authorization; per-request approvals remain in the existing workspace. Tool capabilities are not broadened by the planner.
4. **Verified methods:** Knowledge → Verified methods stores candidate procedures linked to a real task, finding, or source. Only an explicit human review with a verification note marks a method verified. Only verified, same-project or shared methods are suggested; no method is executed automatically.
5. **Tool readiness:** A passive inventory detects Burp, Tor Browser, geckodriver, Nuclei, Claude Code, Codex, ffmpeg, OCR, Cheat Engine, and the platform's native memory bridge. **Detected does not mean connected or working**.
6. **Checkpoint report:** A plan can export a deterministic Markdown report listing observed step results, linked records, source references, and gaps. It does **not** manufacture a verified finding or claim that model output is evidence.
7. **Project cleanup:** Deleting a project now attempts to stop associated active tasks and coding-agent processes before removing scoped ledger entries and reviewed methods.
8. **Interface:** Existing five-section navigation (Chat, Agents, Workspaces, Knowledge, Settings) remains. Chat contains **Plan task**; Agents shows detailed numbered checkpoints and the approval stage; Knowledge adds **Verified methods**. Additional responsive styling and focus outlines are included.

## Backend RPC surface

`plans.create({objective, mode, project_id})`, `plans.list`, `plans.get`, `plans.run`, `plans.approve`, `plans.cancel`, `plans.report`.

`skills.propose({title, method, evidence_id, project_id, tags})`, `skills.review({id, verified, note})`, `skills.list`, `skills.get`, `skills.match({query, project_id})`.

All requests go through the existing **desktop-only** RPC role boundary. The model's narrower ModelTools interface is unchanged; a model response cannot call these RPCs by writing text.

## Regression results

- 266 backend tests passed; 6 skipped. The test runner used an **ephemeral test-only keyring substitute** because this container has no desktop credential store. The substitute is **not included in the release ZIP**.
- `scripts/smoke_production_workflow.py --python` passed as a real local stdio backend process test. It covers indexed Knowledge, plan checkpoints and method verification, a mock local model, an authorized lab action, task history, and restart recovery. No external target or paid API is used.
- All 17 TS/TSX source files passed TypeScript parser checks.
- Full React/Vite bundling was not possible here because the container's npm installation is incomplete. Successful syntax parsing is **not** equivalent to TypeScript typechecking or a running GUI.

## Remaining limitations and release gates

This is a **bounded orchestration foundation**, not an unrestricted autonomous desktop operator. It does not infer arbitrary tools from an LLM plan, control your whole Mac, run live memory operations from Chat, automatically test Tor sites, or automatically certify jailbreak effectiveness. AI evaluation is an explicit workspace handoff. It does not yet offer verified model routing/fallback, parallel multi-workspace workflows, application sandboxing for every CLI, or independently reviewed final security claims.

Before a production release, run the complete npm build, smoke tests, visual/accessibility checks, actual macOS/Windows integrations, restore tests, and signed/notarized installer validation on clean devices.

## Mac startup

Extract into a **fresh** folder. Back up the existing application data before migration. From the new folder:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
backend/.venv/bin/python scripts/smoke_production_workflow.py --python
npm run build
npm run desktop:dev
```

If setup runs `npm ci` for a while, do not interrupt it unless it reports a problem. Your previously used Kaggle endpoint credentials are OS keychain data and are not bundled in this ZIP.
