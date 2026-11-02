# BugHunterClaudex frontend hotfix

Fixed `src/RedTeam.tsx` during prompt-ZIP import. Previously, `await fileBase64(file)` occurred inside a synchronous `() => rpc(...)` callback, causing Vite/Babel to fail parsing the entire AI Testing screen. The file conversion now completes in the surrounding `async uploadArchive` function and the callback receives the resolved string.

No other application behavior was intentionally changed. Retains the original project structure and files.

Run on Mac in the extracted `bughunter` folder:

    npm ci
    npm run build
    npm run desktop:dev

Do not disable the Vite error overlay to hide syntax errors.
