# Guided setup and free web search — 2026-10-31

## Everyday usage
1. In **Chat**, ask a question or describe a task. Upload or select Knowledge if useful.
2. Ordinary questions go straight to your chosen AI. For task requests, the app opens a **Ready to start?** connection check.
3. Complete only the missing connections. The application does not start tests while checking readiness.
4. Continue to the existing authorization/plan review, then watch results under **Tasks**.

## Free public web search
- **Web** in the Chat composer toggles public search for the reply. Explicit instructions such as *search the web* also enable it for that message.
- Default: free `ddgs` metasearch, automatically falling back to Wikipedia if metasearch fails or is rate-limited.
- **No Brave Search key is required**. Brave remains an optional, explicitly selected provider in web research. A saved Brave key never changes the default.
- Search results are **untrusted snippets** supplied to the AI with URLs for citation; the app does not follow links or execute content during search. The model is not retrained and cannot independently browse just because it is loaded on a GPU.
- Search requires an internet connection and depends on public providers. Free services can change, rate-limit or fail. The fallback is narrower than general web search. Do not enter secrets into public search queries.

## Setup checks
- AI connection: verifies that a provider/model and required configuration are present. The **Test AI connection** button uses the existing provider probe; quick preflight does not call the model.
- Web review: requires a saved authorized target scope and a verified built-in policy gateway. Chat offers to start and verify that gateway on demand; Burp remains optional.
- Cart lab: uses the existing local fixture; live-store changes are not automated.
- Memory: macOS bridge availability is detected, but process access and pointer stability require actual tool checks.
- Coding agents: detects installed CLIs; does not claim login, execution permissions or OS sandboxing are verified.

## Build and validation
- Python service module compiled successfully.
- Focused tests added under `backend/tests/test_connection_guide_free_search.py`.
- TypeScript/TSX syntax parsing checked with TypeScript's compiler API.
- Full npm/Vite/Tauri desktop build **not validated** in this environment due to unavailable npm packages. Run `npm ci && npm run build` on your Mac.

## Installation

Extract to a **fresh** `bughunter` directory, then:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run build
npm run desktop:dev
```

Your existing application data should be backed up before replacing an installation.
