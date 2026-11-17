# PentAGI + Haddix Methodology + Commerce Quote Coverage

This integration supports two engines:

- **BugHunter-native investigation (primary):** uses the already-selected BugHunter model through `RunManager` and the existing approval/policy gateway. Claude Code, Codex, Gemini CLI subscriptions, local Ollama/Crucible, and cloud/API providers retain their existing credentials, pricing checks and local-only settings. No extra AI key is necessary.
- **External PentAGI (optional):** runs as a separate, independently managed deployment. The BugHunter connector reads its flow summaries, but does **not** create, run, pause or modify PentAGI flows. CLI subscriptions are not interchangeable with PentAGI's API provider credentials.

The handoff generator uses [Jason Haddix's Bug Hunter's Methodology](https://github.com/jhaddix/tbhm) topic structure to create a scoped investigation checklist. This is an authored adaptation, **not** a verbatim reproduction of Haddix's material.

## TBHM from the Library

The checklist above is linked to 33 TBHM sections built from the two "Bug Hunter's Methodology Live" decks stored in the Library (Day One Recon, Day Two Application Analysis); each section names its deck pages. At runtime the app reads those pages, and the matching talk recordings, to cite real slide and talk excerpts (`backend/bughunter/tbhm.py`). Sources are found by title, so a fresh install with the decks imported works the same; without them the sections fall back to the authored outline and say so.

- Website tasks in chat receive a TBHM outline plus the sections that match the request.
- Both agents have `tbhm_methodology` (outline or one cited section) and `tbhm_progress` (per-project coverage with the deck's green/orange/red legend).
- The investigation started by **Run with selected BugHunter AI** gets the stage-to-section links and the TBHM workflow in its plan.
- RPC: `tbhm.outline`, `tbhm.section`, `tbhm.progress`, `tbhm.record`.

## Use the existing BugHunter AI

Open **Web testing > Tests** and select a saved, scoped project. Click **Run with selected BugHunter AI**. This starts an investigation with `RunManager.start` and a Haddix/commerce plan, using the model currently selected in BugHunter Settings. The run stays within BugHunter's existing action-approval workflow.

The selected provider may be Claude Code, Codex or Gemini CLI subscription, Ollama/Crucible local inference, or an already configured paid/hosted API provider. No `PENTAGI_*` environment variables are necessary for the native run. Existing local-only restrictions and paid-provider preflight checks remain in force.

An independently deployed PentAGI service can reuse a reachable Ollama server when configured separately, but it cannot directly use BugHunter's signed-in CLI subscription sessions. API keys saved in BugHunter's keyring are not exported into PentAGI.

## Configure the optional external PentAGI connection

Deploy PentAGI separately using its [upstream documentation](https://github.com/vxcontrol/pentagi). Create a Bearer token under PentAGI Settings > API Tokens.

Start the BugHunter backend with the following environment variables:

```sh
export PENTAGI_API_URL="https://127.0.0.1:8443"
export PENTAGI_API_TOKEN="your-private-api-token"
# Optional: provide your PentAGI server's trusted CA certificate.
export PENTAGI_CA_BUNDLE="/path/to/ca.pem"
```

The bridge uses the documented `GET /api/v1/flows` endpoint. TLS verification stays enabled. An HTTP origin is accepted only for loopback development. The token is not written to BugHunter's SQLite database or included in RPC responses.

In BugHunter's **Automated cart security lab** page, use the PentAGI panel to inspect recent flows and generate a project-scoped handoff. Export/copy the plan, review it and initiate separate PentAGI activity from the PentAGI UI itself. PentAGI's external agent traffic does not pass through BugHunter's gateway, so enforce its network scope independently.

## Commerce lab coverage

The disposable 127.0.0.1 cart lab now tests:

- Client-controlled item pricing and invalid quantities
- Coupon eligibility, stacking, minimum spend, replay
- Food delivery fee and menu/add-on price integrity
- Synthetic checkout quote: shipping amount, currency, client total and payment-state claims

Checkout tests remain **quote-only**. Neither variant can create an order, process a payment, or fulfill a purchase. Each test is executed against a deliberately vulnerable variant and a fixed control, then correlated with actual action/traffic receipts. Findings remain lab-marked drafts.

## Verification

```sh
cd backend
uv run pytest -q tests/test_pentagi_bridge.py tests/test_checkout_lab.py tests/test_commerce_audit.py
cd ..
npx tsc --noEmit
```

The PentAGI API tests are mocked and never require a live instance. A real PentAGI connection check requires a separately running server and configured credentials.

## Native autonomous pentest engine (no external PentAGI required)

BugHunter now reproduces PentAGI's model on its own stack, so the AI runs PentAGI's techniques without deploying or depending on an external PentAGI server (`backend/bughunter/pentest/`).

- **Faithful catalog** (`catalog.py`): every PentAGI agent role (orchestrator, pentester, coder, installer, searcher, memorist, adviser, enricher, reporter, generator, refiner, reflector, assistant) and every agent function (`terminal`, `file`, `browser`, `web_search`, `search_in_memory`/`store_in_memory`, `search_guide`/`search_answer`/`search_code`, the specialist delegation tools, and the flow-control functions `get_flow_status`/`stop_flow`/`submit_flow_input`/`patch_flow_subtasks`/`wait_flow_completion`) is mapped to the BugHunter tool that performs the same job. Security binaries (nmap, nuclei, ffuf, httpx, sqlmap, hydra, …) run through BugHunter's own adapters; ones not installed are marked `needs_tool`.
- **Faithful state model** (`flows.py`): Flow → Task → SubTask → Action with PentAGI's exact statuses, derived from a real investigation run, its plan and its events.
- **Engine** (`engine.py`): a flow is one autonomous investigation run driven by a PentAGI-style orchestrator prompt built from a profile's phases (recon → enumeration → analysis → vulnerability discovery → confirmation → reporting). Profiles: `web_app`, `ecommerce`, `api`, `network`. Everything runs through the scoped policy gateway, approvals, adapters, TBHM, route review and site monitoring.
- **RPC**: `pentest.catalog|profiles|list|create|start|status|stop|patch_subtasks|submit`. **Agent tool**: `pentest_flow` (both the chat agent and the investigation agent). **UI**: the cart-lab PentAGI panel's "Autonomous pentest (native PentAGI engine)" section starts a flow and shows its Flow→Task→SubTask→Action state live.
- The optional external PentAGI connector still exists for reviewing flows on a separately deployed PentAGI, but it is not required for any testing.
