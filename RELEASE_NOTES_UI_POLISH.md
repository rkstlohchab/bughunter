# BugHunterClaudex — Web Testing and Theme UI Polish

This revision builds on the Project-Centered UI release, preserving its RPC calls, project data, tool permissions and training material.

## What changed

- The Web Testing navigation displays all six sections directly (Start investigation, Targets & scope, Captured requests, Suggested checks, Approvals and Reports). No extra `More tools` disclosure is required.
- The investigation card retrieves the saved `settings.get` provider/model and labels it correctly, distinguishing a Kaggle/self-hosted model from paid APIs. It no longer claims all non-Ollama providers have separate API billing.
- Added a small visible inventory of Burp proxy, managed browser, Nuclei and host discovery on the Web Testing home, with Setup links to the existing connection guides. The inventory does not imply a tool is installed merely because an RPC endpoint exists.
- Added consistent inset spacing for AI Testing and Tor Research form fields, checkboxes, buttons, history and captured source cards, including responsive breakpoints.
- Upgraded `<details>` disclosure styling with clear interactive surfaces, a chevron, hover states, focus rings and better content hierarchy.
- Reworded the Knowledge Library's advanced `Script drafts` access with a plain-language explanation: suggested code is saved for review/copy and is not executed automatically.
- Dark is the default theme on a fresh installation, and Settings now includes a persistently saved Dark/Light switch. Existing explicit light/dark preferences are preserved. Native form color-scheme now follows the selected theme.
- Added Playwright UI regression specs for workspace navigation, saved provider identity, themes and card padding at three viewport sizes.

## Installation (Mac)

Extract this ZIP into a **new** directory; do not overwrite a running app folder. Back up your existing application data. Then from the new `bughunter` folder:

```bash
python3 scripts/setup.py
python3 scripts/build_backend.py
npm run desktop:dev
```

`npm run build` checks TypeScript and generates the Vite frontend. `npm run test:ui -- tests/ui/ui-polish.spec.ts` runs the new browser checks once Playwright and its browser runtime are installed.

## Verification status

- Changed TS/TSX files: syntax-checked using TypeScript's parser/transpiler.
- The archive integrity is validated.
- Full `npm ci`, Vite build and browser screenshot tests **not completed** here: the npm dependency installation timed out in the runtime.
- Live macOS Tauri and tool connectors **not tested** here.

No changes were made to backend authorization, memory read/write operations, or provider credential storage.
