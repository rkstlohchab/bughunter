# BugHunterClaudex — Tor research addition

## Changed

- New `Tor research` sidebar workspace and `backend/bughunter/tor_research.py` backend.
- Local SOCKS5/Tor-only HTTP(S) GET transport, remote hostname resolution, checksum-verified v3 onion addresses, allowlisted user bookmarks, same-origin crawl with explicit budgets, content caps and plain-text extraction. No direct transport fallback.
- Saved sources, operator notes, tags and page captures. Search, review, provenance, deduplicated recapture and Knowledge Library publishing.
- Three agent tools for listing, reading and searching operator-supplied research resources. Research URLs are not target-testing authorization.
- Separate `tor_research` specialist-training curriculum and reviewed-capture-to-draft training import.
- Remote provider sharing is opt-in, controlled independently in the Tor workspace. Local AI inference remains available.
- Added backend fake SOCKS5 integration tests.

## Tested

- Focused Tor/Training/Knowledge Retrieval test set: 23 passed (includes 7 Tor tests).
- Wider knowledge and import test selection: 73 passed, 2 failed due to unavailable optional SQLite-vector/embedding dependency in this container. Those failures were present in the baseline test environment.
- TypeScript syntax checked on changed TSX files with the installed compiler parser. Full type checking/Vite build did not run: Node package dependencies are not cached and offline `npm ci` failed.
- Desktop Tauri build and real Tor network connection not tested in this environment.

## Explicit limitations

No working Tor client is bundled. Supply an installed/running Tor local SOCKS5 service. This is an HTML/text collector, not a full anonymous browser. It does not join or post to forums, sign in or render interactive pages. Captured data is not automatically fine-tuning weights; training exports require reviewed examples and a separate trainer.

See `docs/tor-research.md`.
