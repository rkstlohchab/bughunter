# Production milestones — backup, project scope and release gates

## Implemented

1. **Private data backups:** Settings → Back up your work creates an archive next to the macOS application data folder. SQLite Online Backup API captures a committed database snapshot (including WAL). `sources/`, `artifacts/`, `exports/`, and `workspaces/` are copied with SHA-256 manifests and verification before atomic publication. Backups have restrictive file permissions where supported. Restore accepts only a new/empty directory, rejects symlinks, traversal, duplicates and mismatched hashes, checks SQLite integrity, and remaps paths for files managed inside the app data directory. Restore is intentionally **offline-only**. Keychain credentials are not part of the backup. A ZIP is **not encrypted**: keep it private.
2. **Project-aware knowledge:** New Knowledge imports can specify `project_id` and separate identical documents between projects. Chat includes only knowledge scoped to the selected project plus shared records. Explicit document references belonging to another project are rejected, and a conversation cannot be retargeted silently to a different project. Reviewed specialist examples from AI testing, Tor and training inherit the originating project for retrieval; legacy records derive scope from parent case/workspace/source. Legacy unassigned records remain shared. This is **retrieval isolation**, not yet a full per-project permission/tenant boundary.
3. **Real backend integration smoke:** `scripts/smoke_production_workflow.py` starts a backend over stdio, uses a local fake Ollama endpoint (no cloud or external target), indexes a project document, chats with a cited passage, queues and approves a bounded action against the bundled disposable lab, exports a finding, creates a synthetic memory event in Agents, restarts the backend and verifies interrupted status and persisted messages. An actual browser, macOS memory attachment and Claude/Codex CLI are **not exercised**.
4. **Installer validation:** `scripts/validate_macos_release.py` checks an actual `.app` bundle, Info.plist, macOS code signature, correct executable/sidecar architecture and bundled memory resource. With `--smoke`, it runs the packaged backend integration smoke and the older packaged backend smoke. GitHub Actions adds a manually triggered macOS build gate alongside Python and Playwright tests. This container cannot build or validate an Apple `.app` itself.

## Run on macOS

```bash
cd ~/Downloads/bughunter
python3 scripts/setup.py
python3 scripts/build_backend.py
backend/.venv/bin/python scripts/smoke_production_workflow.py --python
npm run desktop:dev
```

Create a backup from Settings → **Back up your work**. Or via Terminal:

```bash
backend/.venv/bin/python scripts/data_backup.py create --archive ~/Desktop/bughunter-backup.zip
backend/.venv/bin/python scripts/data_backup.py verify --archive ~/Desktop/bughunter-backup.zip
```

To restore, **close the desktop application first**, then use a NEW destination:

```bash
backend/.venv/bin/python scripts/data_backup.py restore --archive ~/Desktop/bughunter-backup.zip --destination ~/Desktop/bughunter-restored-data
```

To test the packaged app:

```bash
npm run desktop:build
python3 scripts/validate_macos_release.py --smoke
```

## Explicit release blockers

- No real macOS Tauri GUI, Tor Browser, Burp, Nuclei, native memory bridge or code-signing/notarization tested in this Linux container.
- Backup is unencrypted and may contain credentials captured inside HTTP research artifacts, even though keychain secrets are excluded. Do not publish it.
- Scoped Chat knowledge is not a multi-user authorization system. Existing project lists, search/export endpoints and old unassigned records require more rigorous tenant separation for a multi-user/cloud release.
- Folder imports linked to files outside the app directory are not copied into backups; import with `copy_original=True` to make them portable.
- Installer code signing is local/ad-hoc, **not Apple Developer ID signing or notarization**. A release requires Developer ID certificates, notarization, a real-device clean install, UI screenshot/accessibility review, and integration exercises.
