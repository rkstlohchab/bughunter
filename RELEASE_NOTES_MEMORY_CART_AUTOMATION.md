# BugHunterClaudex — Memory & Cart Workflow Update

## Implemented, observable paths

- **Chat task intent:** An explicit memory-scan request opens a confirmation modal, then hands the task to the native memory workspace. The workspace attempts to connect tools and starts its agent loop. Native Tauri/Rust policy still requires approval for process attachment and writes. If the memory-model settings are absent or wrong, the tool will stop with a connection error rather than inventing results.
- **Native process scan improvements:** Fixed macOS scan type choices (`i32`, `f32`, etc.); added bounded, read-only `find_pointer_references` (single-hop candidate search) and `verify_pointer_chain` (current-session readback). They never promise stable offsets.
- **Pointer notebook:** Save an observed pointer base/offset list for an offline test process, verify it using live tool results, and record observations from distinct process launches. The notebook is project-scoped and labeled desktop-reported; it does not claim future stability or resolve ASLR module offsets automatically.
- **Cart testing in Chat and Web Testing:** A one-click lab runner sends ten predefined requests to the bundled loopback shopping-cart fixture, using the existing scope, single-use approval, request-budget and evidence pipeline. It compares the intentionally vulnerable and fixed variants; findings remain *observed* drafts pending human review. The task appears in Agents, supports cancellation between requests and is marked interrupted on restart.
- **Real website review:** Chat can create a `web_review` plan in the selected authorized project. Existing per-request scope, approval and gateway controls remain mandatory. This release **does not** automatically mutate arbitrary live stores, purchase products, manipulate real account balances, or bypass checkout.

## What is not implemented or verified

- No online CS:GO/CS2-specific health/entity offsets, ESP overlay, anti-cheat bypass or automatic extraction from protected processes. Protected process attachments may fail on macOS.
- A candidate pointer reference is not a durable pointer chain. The user must identify a module-relative anchor and test after actual restarts. Game updates can invalidate it.
- General-purpose autonomous web crawling or checkout transaction modification is not implemented; only app-owned loopback regression cases are unattended.
- Native Tauri desktop compilation, Mac Mach entitlement behavior, and Windows Cheat Engine integration need real-device validation.
- The memory bridge may need its model endpoint configured separately; it is not guaranteed to inherit the Kaggle connection. Tool execution cannot progress without a working configured model.

## Testing

Run Python tests from the extracted project root after setup:

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_commerce_audit.py backend/tests/test_memory_pointer_helpers.py backend/tests/test_pointer_notebook.py
backend/.venv/bin/python scripts/offline_memory_fixture.py
```

Use another Terminal to start the desktop app. Under Workspaces → Web Testing → Suggested checks, click **Run automatic lab audit**. Under Workspaces → Cheat Engine → native macOS bridge, connect to the process of your own `offline_memory_fixture.py`, scan `i32` value 100, then refine it after issuing `hit` in the fixture terminal. Save a pointer candidate and verify against the fixture process (actual Mach permission is OS-dependent).

Test results from the authoring container are not equivalent to a live Mac release test. A source ZIP, not a signed installer, is provided.
