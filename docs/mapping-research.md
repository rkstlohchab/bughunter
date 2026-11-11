# Cited mapping research in Chat

In the desktop app, choose **Research mappings for my program** in Chat, or send:
“Research memory mappings for the score in the selected program.” The task stays
in the conversation through research, checks and the final cited report.

1. Choose the running program in the inline selector. A request referring to the
   **selected program** can reuse the already attached process. Otherwise the
   workflow waits for a selection; it does not guess a process from its name.
   The desktop starts the configured native Mac helper for this user-requested
   task. It binds checks to the exact process creation identity.
2. The task researches shared/project Library sources, attached files and supplied
   public HTTPS links. The mapping starter enables public web search; typed
   requests respect the Chat web-search toggle. Reply **search the web** to add
   public research to a waiting task. References added in Chat start a new
   research attempt and retain the previous attempt's evidence.
3. The selected model is asked for up to two bounded numeric recipes with exact
   supporting quotes. Module names and all offsets must appear in the quote.
   An unavailable model or rejected proposal leaves an explicitly unverified,
   cited scan plan. Without references, no candidate is generated.
4. Supply the value currently visible in the program, either in the first
   request (**score is 100**) or when asked in Chat. The task checks candidates
   automatically. If the memory setting disables automatic read approval,
   approve each exact check in the inline dialog. No model can approve a check.
5. When prompted, change the value in the program and reply with its new number.
   The task narrows/rechecks and saves the result. Verification requires two
   distinct typed values in an uninterrupted matching sequence in the same
   process instance and executable build. Repeating the same number does not
   verify it. At most six rounds run per research attempt.
6. The final Chat report contains verified mappings, observed addresses and
   values, supporting quotes and source links. The activity card keeps sources,
   failed checks and other candidates available. Download mapping evidence or
   the entire conversation with all research attempts as JSON.

Reply **stop**, use **Stop mapping task**, or use the app's **Stop all** control
to cancel. A stopped task cannot resume checks. **Choose program again** recovers
from process exit; **retry** retries a blocked check. If a scan expired after a
helper restart, reply **fresh scan, value is 100** using the actual current value.
Keep the conversation open during native checks; reopening a conversation resumes
its saved workflow. The task never controls the program to change values itself.

The manual **Tools → Cheat Engine → Research a mapping** panel remains available.
Browser mode can research/export through that panel; native checks currently
require the Mac desktop helper. Windows Cheat Engine receipt support is not yet
implemented.

## Execution and persistence

`MappingChat` coordinates durable chat state and bounded candidate batches.
`MemoryMappings` in the Python application only researches and stores evidence.
It cannot attach or read a process. Model calls are text-only; references and
search excerpts are untrusted evidence. Model fallback routes are not used.

The proposal prompt sends at most four excerpts, each capped at 800 UTF-8 bytes,
and reduces that set to keep the combined instructions and serialized input
within 2,800 bytes. Output is capped at 1,024 tokens. This conservative input
budget leaves room for a reply on a 4,096-token model; provider-added framing or
unusual tokenizers may still impose a smaller limit. Full citation snapshots
remain in SQLite for validation and export. Shortened context does not relax
exact-quote or offset checks.

The desktop creates an exact pending `mapping_check`. The `ce_check_mapping`
Tauri command accepts its identifier and an automatic/manual invocation flag,
retrieves the stored recipe from the backend, and applies the existing read-only
approval policy to automatic checks. It invokes the fixed `verify_mapping`
native-helper tool and exclusively writes a private receipt under
`artifacts/mapping-receipts/`. Chat advances by reconciling the saved native
receipt; the manual panel calls
`memory.mappings.observe` with the identifier. Neither submits a tool result.
The backend validates receipt identity, typed expected values, process/build,
citation integrity and scan bounds before saving observations. Replayed receipts
are idempotent; a newer check supersedes an unfinished check for that candidate.

Receipts provide native-command provenance against model/renderer result
fabrication, not cryptographic attestation against another program running as
the same local user. Executable fingerprints identify files on disk; they do not
attest loaded libraries, scripts, semantic meaning, or stability after restart.
On macOS, executable paths come from the OS `proc_pidpath` interface, rather than
the displayed process command, which may be relative or changed. The API
signature is defined in [Apple's libproc header](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.h).

Mapping scans are separate from raw tool scans, limited to 64 MiB / eight seconds,
20,000 retained addresses, eight scan sessions and 20 readback samples. Partial
coverage is visible. Pointer recipes allow twelve hops and bounded offsets.
Scans never attach, write, freeze or run scripts. Existing OS access restrictions
remain. Absolute-address candidates belong to the observed process instance.

Research activity and candidates are stored in SQLite. Restart recovery marks
unfinished research/checks interrupted; a complete native receipt can be
reconciled after reopening. Stop and project deletion prevent further checks.
Deleting a project removes its mapping records/receipts; previously exported
reports remain subject to the app's existing export-retention behavior.

## Validation

- Backend mapping tests cover citation integrity, scoped retrieval, model outage,
  invented offsets, exact receipts, distinct-value confirmation, mismatches,
  interrupted recovery, scan narrowing and process changes.
- Native helper tests cover bounded read-only scans, pointer resolution, short
  reads, process/build changes, and a real native read of an owned value.
- Playwright covers cited candidates, native command invocation, persistence,
  export, failure messages and narrow screens using mocked desktop transports.
- Rust tests cover receipt identifiers, exclusive writes and private file mode.

These tests do not establish access to an arbitrary external program, Windows
adapter support, or a live provider's proposal quality. macOS can refuse external
attachment even when the helper's own-memory read test succeeds.

### When selecting a program appears to do nothing

The selection button now shows **Connecting…**, prevents overlapping attachment
attempts, and saves an attachment failure in the conversation. Previously a
successful status refresh erased the native error, leaving the program picker
looking unchanged. A failed attachment now blocks the task visibly; **Choose
program again** returns to selection. Refreshing or reopening the conversation
preserves the failure, and does not retry attachment automatically.

On this Mac, selecting Among Us returned `task_for_pid(...): KERN_FAILURE(5)`.
No memory check ran. Keep System Integrity Protection enabled: ordinary app use
does not require disabling it, and this error alone does not identify SIP as the
cause. Debugger entitlements and the target's signing/runtime restrictions also
affect attachment; [Apple describes the debugging entitlement here](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.debugger).
The UI correction does not grant access to a protected process.

### Local validation: 4 November 2026

- Full backend regression: **584 passed, 1 skipped**.
- Full browser regression: **33 passed**, including automatic Chat checks,
  explicit process selection, inline approval, stop, cited reports, export,
  reload persistence and narrow-screen failure recovery.
- Rust: **28 passed**. Routing: **32 passed**. TypeScript typecheck passed.
- Support/packaging contracts: **29 passed**.
- Native helper: **20 tests run, 4 skipped** because macOS refused external
  attachment. The real owned-memory read and all selection/identity tests pass.

Browser workflow tests use a mocked native transport; backend tests use controlled
model proposals and native receipts. They do not prove a live model's mapping
quality or access to an arbitrary external program.

### Continuation checks: 6 November 2026

The full regression report is in `build/verification/2026-11-06/regression/`:
611 backend tests passed (two skipped), 74 supporting tests plus 16 subtests,
36 browser tests, 32 routing checks, 28 Rust tests, and a successful frontend
production build. Native contracts ran 23 tests with four external-attachment
skips. The focused mapping/chat/native suite passed all 62 tests.

`scripts/smoke_mappings.py` exercises cited research, a real OS read of its own
allocated value, a second changed value, receipt reconciliation, export, and
reopening storage. Its default proposal is a fixture; `--model` explicitly uses
one local Ollama model. It limits native reads to its own allocation and never
touches an external program. Its receipt transport is a test harness, while the
production receipt writer is verified separately by Rust tests.

The live 9B Ollama attempt on this Mac timed out during model startup. The fallback
scan plan completed the owned-value readback and evidence checks, but no live-model
proposal was accepted. This result is recorded in
`build/verification/2026-11-06/mapping/live-ollama-workflow.json`.
