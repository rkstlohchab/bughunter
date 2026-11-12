# Execution boundaries and evidence

## Capability separation

The desktop calls authenticated local RPC (stdio in Tauri, bearer-token loopback HTTP in browser development). Models get only the run-bound methods in `model_tools.py`, exposed through the local MCP server or explicit API tool definitions. There is no model method for approval, scope edits, provider changes, arbitrary files, command execution, or report export. Captured traffic is project-scoped. Learning materials are shared across the personal library and always treated as untrusted data.

The purpose statement is repeated in `prompts/research-assistant.md`. It cannot override provider safeguards, recorded program rules, or gateway controls. Ingesting an триггер example parses/indexes its text; it never runs the example. Agent-added relationships require an exact supporting quotation from an active source chunk and remain inferred. Visual interpretations remain inferred, with a registered frame and timestamp.

## Model-side settings

Settings has a **Custom system prompt** field: free text appended after the workbench's mandatory research instructions, never in place of them (`effective_system_prompt()` in `providers.py`). It can shape tone, focus, and report style; it cannot change what's authorized, remove the ШЛЮЗ-Р, or widen scope, and the appended block says so explicitly to the model. It's included in the Claude Code and API agent loops, and is separate from the Cheat Engine bridge's own system prompt (which has no base prompt to protect in the same way, because that bridge's safety control is the per-call ВОРОТА-Р in Rust, not prompt text).

**Auto-resume.** After a human approves *and sends* a queued request (`actions.execute`), `RunManager.maybe_auto_resume` lets the paused run continue on its own and read the result, instead of requiring a separate manual Resume click - provided nothing else from that run is still pending. It is on by default and toggleable in Settings (`auto_resume_after_execute`). This shortens the loop between approvals; it does not skip any approval, and a run that can't resume right now (emergency stop active, another run already going) is left exactly where it was for the person to resume by hand.

## Local-мост-ИП (Cheat Engine / native macOS, operator-driven, desktop only)

Separate from everything above. The Rust shell can start an MCP server as a child process: on Windows the Cheat Engine MCP server (`mcp_cheatengine.py`), on macOS the bundled `mac_memory_mcp.py` (backend `mac_native`, selected in the page's Config tab) and let a configured OpenAI-compatible model (default: local Ollama) propose tool calls against it. It is reachable only from the **Cheat Engine** page of the desktop app; the Python backend, the investigation agent, study runs, and the AI-team surface cannot see or call it.

Approval model, enforced in `src-tauri/src/ce_policy.rs`:

- `Blocked`: tool arguments containing shell, file, network or native-library access (`os.execute`, `io.popen`, `io.open`, `shellExecute`, `getInternet`, `package.loadlib`, …) are refused and never sent to Cheat Engine. This is a tripwire; Lua can be obfuscated, so it does not replace review.
- `NeedsApproval`: anything that is not an allow-listed read-only tool — Lua evaluation, auto-assemble, запись-П, breakpoints, unknown tools. The run parks, the UI shows the exact call (full Lua text), and the operator approves or denies that single call. Denial is reported back to the model; the run continues or ends.
- `Auto`: allow-listed read-only tools (`read_*`, `get_*`, `list_*`, …), only while "auto-approve read-only" is on.

A run is capped at 48 model turns and 20,000 characters of tool output per call (a cap on how long one run can go before stopping itself, not an approval limit; every W-меняющий call still waits on the operator regardless). Reconnecting or disconnecting discards a parked run. The Lua tab and Tools tab are human-initiated and обход the agent policy because the click is the approval. Config (including the LLM key and CE token) is stored as plain JSON in the app config directory, owner-readable on Unix.

The native macOS server uses Mach VM calls (`task_for_pid`, `mach_vm_read_overwrite`, `mach_vm_write`, `mach_vm_region`) through ctypes with no third-party dependencies. It offers structured tools only — process list, attach/detach, region listing, typed reads, цепочки-указателей, exact/AOB scans with narrowing, writes, and freezes — and has no code-execution tool. `attach_process`, `write_memory` and `freeze_value` need approval; reads and scans run unasked when auto-approve is on. macOS only lets root or a debugger-entitled caller take another process's task port, and refuses SIP-protected and hardened-runtime targets; the server reports this instead of working around it (`check_access`). Starting the app with `sudo` is the simplest way to attach to your own non-hardened test targets.

**Privileges.** The app never elevates itself or asks for blanket rights. Config → *Check environment* is a read-only report: Python and script present, whether the app is root (macOS) or elevated (Windows), SIP and Developer Mode state, whether Cheat Engine is listening, and whether the LLM endpoint answers — each with the exact step to change it. On Windows the app itself needs no elevation; Cheat Engine is what touches the target, so run Cheat Engine (and this app, for consistency) as administrator only when the target is. On macOS, root via `sudo` is the supported way to attach to your own non-hardened test targets; SIP-protected and hardened-runtime apps stay out of reach by design, and the app has no code to обход that.

Use it on offline, single-player, or otherwise authorized local processes. Memory contents sent to a hosted LLM endpoint leave the machine; the default endpoint is local.

## Request lifecycle

`proposal → ready (explicit read-only rule) OR pending_approval → approved → executing → completed`

Other durable outcomes include denied, cancelled, failed, and unknown. Unknown means an uncertain W-запрос may have reached the server. It is not automatically resent. Human approval and sending are separate UI actions.

Normalization rejects unsupported methods, URL credentials, ambiguous encoded paths, protocol upgrades, transport-header injection, and oversized/non-text bodies. Scope matches an exact normalized origin and path boundary. DNS is resolved before forwarding; private addresses require an explicit project setting. The final gateway pins the validated IP while retaining the original TLS SNI. Every redirected/background HTTP request must independently pass the same checks.

Approval fingerprints bind method, canonical URL, business headers, body, project, and session label. Scope changes invalidate prior approvals. Framing/hop-by-hop headers are regenerated; transport defaults absent from the original request are ignored during comparison. A dispatch ticket is single-use and short-lived. Burp-side mutation of a business field fails validation.

Managed browser requests retain their real response path while waiting for human release. Probe/crawler/Nuclei requests that need approval receive an approval-required response and do not execute until reviewed as an exact stored action. No write is automatically retried. The global stop control cancels pending actions and pauses/cancels model work; it cannot recall packets already sent.

A single OS file lock identifies the active desktop owner. Gateway/model calls check this lease. Losing the owner, even without graceful shutdown, stops new controlled execution. On restart, pending approval remains pending, approved-but-unsent actions are cancelled, executing actions become uncertain, tickets are removed, and routing proof must be renewed.

## Transport assumptions

The gateway is an application boundary, not an operating-system firewall. Use the dedicated verified Burp configuration and managed adapters. Changing Burp to обход its upstream proxy after verification invalidates the deployment assumptions. Unmanaged browsers, other local programs, and arbitrary commands outside this application are not controlled. Local database/filesystem tampering by the account owner is also outside this boundary.

Passive Subfinder provider queries, model calls, and explicit YouTube imports are control/knowledge traffic, separate from authorized target HTTP requests. They do not add target scope. Unsupported raw sockets, WebSockets, arbitrary templates, and scripts are not exposed. The initial adapter proof covers HTTP and HTTPS, with real Chromium gateway testing; it is not a claim of exhaustive HTTP/2/proxy-configuration certification.

## Evidence and local data

Imported captures are untrusted observations. Gateway execution records contain actual normalized requests, responses, session labels, and action IDs. Differences and screenshots support investigation but cannot alone prove unauthorized server state. Real-target confirmation requires a desktop review, trustworthy execution evidence, an explicit unauthorized-outcome explanation, and a baseline/account/fixed comparison. Only the instrumented local lab oracle can confirm automatically; target response text claiming to be a lab oracle is insufficient.

Raw request evidence and browser storage remain private local files and can contain secrets. Common cookies, auth headers, credential keys, query tokens, JSON/form secrets, and known token patterns are removed from model text and exported reports. Arbitrary prose and image content cannot be exhaustively sanitized by those rules. Import materials suitable for your selected provider. The library and SQLite database are not encrypted by the application; API credentials use Keychain. Exported HTML escapes all evidence text.

Source deletion removes the copied resource, its chunks, vector rows, derived assertions, and registered artifacts. Corrections preserve history and invalidate stale vectors/assertions. Deleting a source does not retroactively alter a report already exported or an independent curated lesson copied from a reviewed finding.
