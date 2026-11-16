# Game runtime implementation plan and agent handoff

Plan ID: `game-runtime-v1`. Revision: `1`. Prepared: 2026-11-14.
Inspected source baseline: `8187f411ab50878cab555a88fcbe607af2cacc23`.

This is an implementation specification, not a statement that these features
have shipped. The companion [phase manifest](phase-manifest.json) starts every
implementation phase at `not_started`. Earlier inspection and tests are baseline
evidence only. Changes to this document do not implement the game runtime.

## 1. Intended result and current evidence

The user should be able to describe a game task in chat. The app should select the
actual installed game, consult the linked course, inspect the target, discover
and validate the relevant data, run the requested feature, and explain the
observed result. Saved mappings should survive process restarts when their
resolver has passed restart checks. Game updates must invalidate incompatible
profiles instead of continuing with stale addresses.

Delivery order and decisions already established:

- Separate local Mac and Windows installations; neither requires the Mac to
  control the Windows machine remotely.
- Among Us local no-cooldown is the first real game acceptance milestone.
- Desktop boxes, enemy lines and health follow verified entity/camera readers.
  Numerical health is shown only for games where that field actually exists and
  has been validated; Among Us alive/dead status is not numerical health.
- BGMI is a subsequent experimental target: a non-rooted Android phone with the
  overlay on the phone. Rooted access, a mirrored computer overlay and image-only
  detection do not satisfy this requirement.
- Keep the existing model/provider selection and harness. Improve the game path
  without redesigning the website or replacing the agent loop.
- Uploaded knowledge becomes searchable, cited procedures and verified examples.
  Importing a course does not train model weights or establish perfect knowledge.
- Exact Windows and Android builds are runtime inputs to qualification. Their
  absence does not justify invented offsets, device properties or success claims.

### Observed baseline

The app has useful parts but the inspected code does not establish the complete
requested workflow:

- Game chat and course retrieval are disconnected in important cases. Earlier
  cooldown runs had no project/source binding. The course project contained 17
  sources, 597 chunks and 738 candidate procedures at inspection. Candidates are
  explicitly unverified; these counts are an observation, not a release gate.
- Retrieval in `library_context.py` depends on source-name matching and project
  filters. An operation request can miss the course even though it was imported.
- Existing memory tools, mapping records and pointer notes provide foundations.
  They do not by themselves prove restart-stable game profiles.
- The legacy Among Us path includes assumed layouts, an unacknowledged socket
  write, and a runner that ignores a failed cooldown write.
- The native MCP connection currently discards notifications. It needs a single
  reply/notification dispatcher before live frame streaming is added.
- `Store.put()` does not compare record revisions. Queue claims, cancellation and
  completion need transactional state changes rather than get/modify/put.
- The Windows reader needs explicit pointer-width-correct native declarations,
  handle cleanup and read/write receipts. The bundled native script has no
  equivalent Windows implementation.
- The current mobile memory adapter uses rooted Zygisk access and a computer
  overlay. It does not implement the selected non-root/on-phone design.

Previously observed checks: 76 focused backend tests and 42 routing tests passed;
the native Mac self-process read/write/restore check passed. A separate-process
restart check failed with `task_for_pid ... KERN_FAILURE (5)`. Those results were
not rerun merely to write this plan. None proves the requested live game feature.

The installed Mac target was `/Applications/AmongUs.app/Wrapper/AmongUs.app`:
Among Us 2026.11.3, build 2, ARM64, wrapped iPhoneOS application, Unity
2022.3.62f3, IL2CPP metadata version 31. The module is
`Frameworks/UnityFramework.framework/UnityFramework`; do not assume a conventional
macOS `Contents/MacOS` layout. Observed `killTimer` and `SetKillTimer` strings are
names in metadata, not field offsets. The qualification report must rehash the
installed files before reusing these observations.

## 2. Rules for agents, ownership and handoff

### Roles

These are work roles, not a requirement to run all agents simultaneously. Use the
coordinator plus at most three specialists at a time.

- **C — coordinator/integrator:** owns contract versions, shared composition
  files, dependency order, migrations, packaging and final acceptance.
- **K — knowledge specialist:** course binding, retrieval, applicability,
  corrections, cited procedures and retrieval evaluation.
- **N — native specialist:** Mac/Windows memory adapters, helper protocol,
  connection ownership, cancellation and resource cleanup.
- **R — runtime/mapping specialist:** durable sessions, operations, scans,
  pointer recipes, verification history and feature lifecycle.
- **T — target specialist:** exact-build analysis, field semantics, game profiles
  and reproducible target experiments.
- **U — overlay/mobile specialist:** frame validation, projection, desktop and
  on-phone rendering and lifecycle behavior.
- **V — verifier:** reviews a different agent's work, replays decisive checks and
  accepts or rejects phase evidence. The implementation author cannot provide
  the only acceptance review.

### File ownership

Use the existing [team protocol](../agent-team.md). Read status and notes, claim
literal canonical paths, keep the claim key private, and update with the latest
revision. Claims do not expire automatically. Stop editing before releasing a
claim. A runtime execution lease described below is a different mechanism.

Each work ticket lists actual files or directories, not the role name alone.
Avoid claiming broad parents such as `backend/bughunter` or `src-tauri/src`.
Use separate worktrees for concurrent implementation when practical.

- C alone integrates `app.py`, `harness.py`, `operator_tools.py`, `game_lab.py`,
  `__main__.py`, `src/Chat.tsx`, `src/taskIntent.ts`, `src/api.ts`, `src/types.ts`,
  `src-tauri/src/lib.rs`, dependency manifests, lockfiles and CI configuration.
- K owns the new `game_runtime/knowledge.py` and its focused tests. Changes to
  existing retrieval modules are explicit additional claims.
- N owns `memory_engine/`, the native helper shim, `game_bridge.rs` and the
  affected `ce_core.rs`/`ce_mcp.rs` transport code.
- R owns `game_runtime/broker.py`, `service.py`, `mappings.py`, `features.py` and
  their tests. Migration changes in legacy mapping modules require named claims.
- T owns target profiles, qualification tooling and claimed game reader files.
- U owns `game_overlay.rs`, `GameOverlay.tsx`, frame adapters and phone renderer
  files. Only one agent owns the shared frame contract at a time.
- V owns acceptance scripts/reports under a separate claim; reviews source
  read-only unless a repair is explicitly handed back or assigned.

Specialists send C an integration request containing exact imports, dispatcher
entries, tool registration, configuration and required tests. They do not edit
shared files concurrently. Contract changes require all affected bindings and
fixtures in the same integration change.

### Required handoff from every agent

Provide all of the following before releasing the task:

1. Phase/check IDs, plan revision, input artifact hashes, base revision and final
   code revision or dirty-tree patch hash.
2. Owned files and actual changed files; any integration request still pending.
3. Implemented behavior and the important decisions fixed by this plan.
4. Exact commands, working directory, environment and raw results for each check.
5. Separate implementation status and target verification status.
6. Remaining failure, its earliest failing step, and the next reproducible action.
7. Evidence directory, restoration/cleanup result and any outstanding resources.
8. Receiving role and the exact artifact it should consume next.

An agent saying "done" means ready for review. V must accept the required checks
before C records the phase as verified. An unavailable device is `blocked`, not
`passed`, and a skipped check cannot satisfy a required gate.

## 3. Code architecture and fixed interfaces

### Modules and integration

Add `backend/bughunter/game_runtime/` with these responsibilities:

- `contracts.py`: schema validation and stable error codes.
- `service.py`: game RPC facade and session orchestration.
- `targets.py`: target identity, installed-build discovery and capability reports.
- `knowledge.py`: explicit collection bindings and task-specific knowledge plans.
- `broker.py`: transactional native operation queue, leases and receipts.
- `mappings.py`: immutable recipe revisions, observations and invalidation.
- `features.py`: start/verify/monitor/stop lifecycle with cleanup semantics.
- `experiments.py`: bounded experiments and linked evidence records.

Add `backend/bughunter/memory_engine/` with protocol/server, sessions, scans,
pointer resolution and OS backends. Extract reusable code from the bundled
`mac_memory_mcp.py`, retaining a compatibility entry point. A dedicated
`--memory-mcp` mode must dispatch before constructing the main `Application`;
it must not take a second desktop-owner lock or start unrelated app services.

Rust `game_bridge.rs` owns native helper connections and execution;
`game_overlay.rs` owns overlay window lifecycle. TypeScript
`gameRuntimeApi.ts` contains typed API calls, `GameTaskActivity.tsx` shows task
progress/evidence, and `GameOverlay.tsx` draws validated frames. The coordinator
wires these into the existing chat and harness.

Reuse `rea.py` for analysis sessions and tool discovery. Reuse the existing
desktop automation and `process_runtime.py` abstractions for installed tools,
launch/wait/stop and subprocess cleanup. Add structured tool adapters where
possible; use UI automation when required by a tool. Record readiness, command
results and postconditions, including unavailable Accessibility/automation grants.
Launching Cheat Engine or Ghidra is not proof that analysis succeeded.

Do not introduce a second game reasoning loop. The existing operator/harness
uses typed game tools. The legacy `ModelTools` investigation path and unrelated
website behavior do not acquire these tools accidentally.

### Public and internal RPC families

Extend the existing `gamelab.*` facade without breaking current callers:

```text
gamelab.targets.list / inspect
gamelab.sessions.start / get / stop
gamelab.research.start / status / evidence
gamelab.scans.start / refine / results / cancel
gamelab.mappings.save / list / verify / export / import
gamelab.features.start / status / stop

Internal desktop bridge only:
gamelab.native.claim / dispatched / complete / heartbeat
```

Long operations return durable IDs immediately; status reads return progress and
receipts. Internal claim/complete calls are bound to the registered executor;
they are not model tools. Memory values and addresses must never be accepted as
verified merely because the model supplied them.

At each app start, Rust generates a 256-bit random bridge credential and sends
it in a one-time private bootstrap exchange before accepting renderer RPCs. The
Python service retains it only in memory. Rust's public renderer RPC proxy rejects
the bootstrap and `gamelab.native.*` methods; only its internal bridge can call
them. Every internal call validates the credential, registered executor epoch,
helper identity and operation binding. Registration binds the helper PID/birth
identity and target identity; an executor ID supplied in JSON is insufficient.
Rotate the credential on app/sidecar restart, redact it from logs, and never
expose it to the model, renderer, settings or saved reports. Browser development
mode has no native execution unless launched through the same private bridge.
This establishes application-level receipt provenance, not independent attestation
against a fully compromised local host.

Use `snake_case` wire fields consistently across Python, Rust and TypeScript.
Put authoritative JSON schemas in `contracts/game_runtime/`, with shared valid
and invalid fixtures. Existing UI types may wrap these; they must not diverge.

Minimum records:

```text
GameTarget:
  id, game_id, platform, runtime, architecture, pointer_width,
  executable_or_package, version, build_fingerprint, modules, capabilities

GameSession:
  id, target_id, harness_session_id, session_generation, stop_generation,
  process_instance_id, knowledge_collection_ids, state, last_error

MappingRecipe:
  id, schema_version, revision, target_fingerprint, name, value_type,
  root, steps, preconditions, evidence_ids, verification_status

NativeOperation:
  id, request_hash, session_id, session_generation, stop_generation,
  process_instance_id, target_fingerprint, kind, effect_class, arguments,
  preconditions, status, revision, executor_id, executor_epoch,
  lease_expires_at, attempt_id, result, error, evidence_ids

FeatureRun:
  id, session_id, feature_id, recipe_revisions, configuration, state,
  readback_status, behavior_status, cleanup_status, evidence_ids
```

Represent addresses, module RVAs and signed pointer offsets as strings with a
documented numeric format; JavaScript numbers must not truncate 64-bit values.
Separate module-relative roots from process-bound absolute addresses. Pointer
steps explicitly identify add-offset and dereference operations and width.
Missing values, unresolved placeholders and legitimate zero offsets are distinct.

### Execution ownership, cancellation and transport

The harness owns task/provider/grants and global stop generation. The broker
owns durable operations. Rust owns one app-wide executor/connection per target
process, keyed by machine/device identity plus process birth identity. Multiple
chat sessions share serialized access through that owner or receive a busy result.
Allow one mutating feature owner per process; read-only subscriptions may share
the executor. Conflicting feature writes receive a busy result until the existing
feature stops. A feature may coordinate several fields within its own lifecycle.
Legacy CE calls and old readers must use that owner or return a clear busy
status; they cannot independently attach and run write loops on the same target.

Implement specialized SQL tables and transactions using
`Store.connection(immediate=True)`. Claims/completions compare revision,
executor epoch and attempt ID. Use the following transitions:

```text
queued -> claimed -> dispatched -> succeeded | failed | outcome_unknown
queued -> cancelled_before_dispatch
claimed -> cancelled_before_dispatch (executor acknowledgement required)
dispatched -> cancellation_requested -> terminal execution outcome
```

Record dispatch intent before a native mutation. An identical completion is
idempotent; conflicting receipts are rejected and retained as diagnostic events.
Old session/process results cannot advance a new feature. A lost write response
becomes `outcome_unknown`, never an automatic write retry. A read retry uses a
new attempt after confirming the original process identity.

Default control polling: 100 ms; executor heartbeat: 1 second; lease: 5 seconds.
A lease expiry allows recovery investigation, not immediate target takeover.
The old helper must exit or be terminated and reaped before a replacement writes.

Stop invalidates ordinary new work, cancels undispatched operations, signals all
scans/feature loops, and clears overlays. Allow 2 seconds for quiescence. If the
helper acknowledges, permit only the feature's previously declared cleanup
operation, bound to the original process/recipe and current preconditions, for
at most 2 further seconds; then close the helper if no unaffected subscription
remains. This cleanup lane does not
revive the stopped task or accept a new model-proposed action. If quiescence or
cleanup times out, terminate/reap the helper within a further 2 seconds. If
termination fails, retain ownership and mark the target unavailable. If the
helper already exited or target identity changed, cleanup is unavailable rather
than retried on a replacement process. Record uncertain writes separately. Never
silently write an old countdown value back after elapsed time.

`features.stop` revokes only that feature; `sessions.stop` revokes that session's
operations and features; `system.stop` revokes every session and closes all
helpers. Increment the corresponding session/global generation. A forced helper
termination interrupts every subscriber to that process; mark those sessions
interrupted, clear their frames and require reconnection rather than pretending
their operations continued. Include shared-reader/active-writer Stop cases in
the cancellation acceptance tests.

Refactor helper transport to one permanent stdout reader that separates RPC
replies from versioned notifications. Use one bounded writer queue, prioritized
stop/control messages and a capacity-one latest-frame channel. Default maximum
message size is 4 MiB; larger scan results use pagination/artifact references.
Malformed messages fail their operation; helper exit resolves pending requests.

Keep the main Python application stdout response-only, as required by the
[implementation contract](../implementation-contract.md). Stream frames directly
from the helper through Rust to the overlay, not through SQLite or model turns.

### Frames and completion semantics

Frames carry schema/session/process/build identity, sequence, producer timestamp,
viewport, camera, entities and field validity. Document matrix layout,
handedness, coordinate units, clipping and physical/logical pixel conversion.
Use receiver monotonic arrival time for staleness; clocks across processes or
devices are not assumed synchronized. Drop old sequences and mismatched frames.

Health, team and visibility are nullable. A renderer cannot fill unknown data
with `100`, `0`, a guessed team or a claimed visibility state. Legacy file/UDP
outputs require session-specific paths/ports; no shared global frame file.

User-visible task states are discovering, consulting knowledge, investigating,
validating, applying, verifying and monitoring, with needs-input, blocked,
failed and stopped outcomes. `running` means the loop is active; `verified`
requires accepted behavior evidence. A spawned PID alone satisfies neither.

## 4. Research packages and bounded decisions

Every experiment records question, exact inputs/hashes, pinned tool/version,
method, expected observation, actual observation, artifacts, conclusion and next
action. "Not supported on this build" is a useful result; it is not feature
success. Keep observations, hypotheses and derived layouts separate.

- **R1 — course usefulness:** find the actual imported lessons for value scans,
  changed/unchanged scans, pointer chains and cooldown investigation. Measure
  retrieval before changing prompts; preserve project isolation.
- **R2 — Mac target access:** qualify the installed wrapped iPhoneOS build and
  distinguish self-process access, disposable external access and target access.
  Test the existing documented access path first. If a platform-correct local
  connector is required, qualify an isolated test copy and record SDK/signing/
  startup requirements. The current machine has Command Line Tools, but the iOS
  SDK was unavailable at inspection. A host macOS library is not automatically
  an iOS-compatible module. See Apple's [debugger entitlement documentation](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.debugger).
- **R3 — exact Among Us layout:** start with the inspected module and metadata,
  use [Cpp2IL 2022.1.0-pre-release.21](https://github.com/SamboyCoding/Cpp2IL/releases/tag/2022.1.0-pre-release.21)
  as the first pinned qualification tool, record binary checksums and outputs,
  and confirm support for metadata version 31 on this input. Tool failure is a
  recorded parser gap; it does not justify filling offsets from another build.
  Reuse the existing RE analysis integration to inspect unresolved structures.
- **R4 — cooldown meaning:** distinguish match setting, local countdown, button
  readiness and the rule that actually accepts an action. Observe baseline,
  change one variable, and verify actual gameplay in the selected local mode.
- **R5 — Windows parity:** qualify process bitness, handle rights, module
  enumeration, partial reads and process lifetime. Follow the documented
  [ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory)
  and [VirtualQueryEx](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualqueryex)
  contracts. A Mac result does not certify Windows.
- **R6 — non-root BGMI:** qualify device/OS/ABI/package, launch, real data access
  and on-phone graphics in that order. The selected experimental approach is an
  instrumented test package with an in-process connector; startup and useful
  data access remain unproven. Account for the Android [application sandbox](https://source.android.com/docs/security/app-sandbox)
  and [signing model](https://developer.android.com/studio/publish/app-signing).
  Do not assume that a separate non-root app can read another app's memory.
- **R7 — rendering:** qualify current Tauri transparent windows, game-window
  tracking, DPI and [click-through input behavior](https://v2.tauri.app/reference/javascript/api/namespacewindow/#setignorecursorevents).
  Phone drawing must be tested on the phone, including rotation and touch.

Retain existing lockfiles. Any new analysis/mobile dependency must have an exact
version and artifact checksum in the qualification report before integration.
An unresolved dependency pin is a failed qualification gate, not an instruction
for another agent to guess later. No phase promises permanent compatibility or
an account/ban outcome.

## 5. Implementation phases and acceptance

The IDs below are stable and appear in `phase-manifest.json`. All checks are
required unless explicitly marked conditional. Platform-specific checks are
reported separately. A completed investigation may honestly report a blocked
target; a feature gate requires the target capability itself to pass.

### P00 — Freeze contracts and create the verification harness

**Owner:** C; reviewer V. **Prerequisites:** source baseline and existing team
status. This phase is the prerequisite for parallel implementation.

**Work:** define schemas/fixtures, error codes, state transitions and ownership
from section 3. Create a disposable target fixture with independently known
values, movable allocations and controlled restarts. Record fresh baseline
checks. Implement `scripts/verify_game_plan.py` to validate phase reports,
artifact hashes, dependencies and required check IDs. It must not infer gameplay
success from a screenshot or a model statement.

**Outputs:** contract version 1, fixture, report schema, verifier, baseline report
and claimed work tickets. Future report storage is described in section 6.

**Expected outcome:** every agent has the same interfaces and definition of done.

**Pass checks:** `P00.contracts` shared valid/invalid fixtures agree in all three
languages; `P00.fixture` independent oracle detects intentional wrong addresses;
`P00.report_validator` missing/failed/forged-reference evidence cannot produce a
pass; `P00.baseline` captures exact environment and baseline failures without
claiming they are new regressions.

### P01 — Make the uploaded course affect game investigations

**Owner:** K; reviewer V. **Prerequisite:** P00.

**Work:** implement collection binding before a game run starts; search requested
operations and related concepts without requiring a lesson title. Persist a
`knowledge_plan` with excerpts, source/chunk locators, applicability, proposed
steps, unsupported assumptions and planned verification. Preserve private-project
filters. Keep user corrections, imported facts, inferred procedures and executed
results distinct. Invalidate affected procedures when their sources change.
Remove the chat behavior that drops an active game task merely because a
follow-up mentions "course" or "library".

**Outputs:** knowledge service, linked collection metadata, retrieval fixtures,
coverage report and original cooldown-request trace.

**Expected outcome:** the AI can show which course instructions informed the
investigation and why instructions for a different platform were rejected.

**Pass checks:** `P01.retrieval` at least 27 of 30 answerable benchmark requests
return a reviewed relevant passage within the top five; `P01.negative_cases` an
additional 10 missing-answer/platform-conflict/correction cases produce the
expected gap or qualification; `P01.citations` all emitted citations resolve;
`P01.isolation` unlinked chats cannot access another project's private course;
`P01.behavior` a controlled fixture demonstrates that retrieved instructions
change the investigation steps correctly. Expected passages are authored/reviewed
before scoring, not chosen by the model being evaluated.

`P01.correction_reuse` retrieves an original procedure, saves a correction,
invalidates dependents, repeats the task and proves that the corrected procedure
is used with provenance while the superseded instruction is excluded.

### P02 — Identify exact targets and qualify tools

**Owner:** T, with N for access experiments; reviewer V. **Prerequisite:** P00.

**Work:** implement `targets.py` and `scripts/qualify_game_target.py`. Discover
installed targets/modules, build hashes, runtime, architecture and process birth
identity. Run R2/R3/R5 and gather R6 device inventory when available. Discover RE
tools through the existing RE integration, not PATH alone. Pin new tool artifacts.
Produce separate Mac, Windows and Android records. Fix conventional-Mac-path
assumptions in the adapter through the coordinator's integration request.

**Outputs:** `target_qualification.json`, tool manifest, static analysis evidence,
runtime-access receipt or exact denial, and unresolved-input list.

**Expected outcome:** the app knows which build it is examining and which
operations currently work on it.

**Pass checks:** `P02.identity` wrong architecture/module/build is rejected;
`P02.derivation` static candidates reproduce from hashed inputs and pinned tools;
`P02.capability_report` distinguishes static-only, runtime-access-blocked and
runtime-ready. `P02.target_memory_access` is a separate capability check for
each live target: prove a bounded read from the exact game process/build and
record `access_mode=external|in_process`. A read from the workbench or a fixture
does not qualify the game. P02's investigation may finish with this capability
blocked; P06/P09 cannot. P03 separately tests external desktop adapters.

### P03 — Build a dependable native memory and tool service

**Owner:** N; reviewer V. **Prerequisite:** P00; use P02 records when available.

**Work:** extract the memory service, implement Mac and Windows adapters, correct
Windows native signatures/handle sizes, add acknowledged writes and structured
partial-read errors, and centralize connection ownership. Implement the permanent
RPC/notification reader, bounded queues and helper-exit handling. Replace POSIX-only
subprocess cleanup where Windows requires its own process-tree mechanism. Keep
the old CE panel functional through the shared owner. Read/write sessions bind
to process birth identity, not PID alone.

**Outputs:** packaged-capable helper, transport adapters, native fixtures,
resource-lifetime tests and actual Mac/Windows capability reports.

**Expected outcome:** the AI can receive reliable memory results and control
installed tools through one execution path with observable failures.

**Pass checks:** `P03.memory` read/write/readback/restore succeeds on a separate
disposable process on each available OS; `P03.lifetime` partial reads, exit,
PID reuse and stale handles fail correctly; `P03.transport` interleaved frames,
replies, malformed packets and disconnects do not lose/corrupt replies;
`P03.ownership` competing attach/runner requests cannot create two executors;
`P03.tool_control` tool launch/readiness/stop has real postcondition evidence.
Mac self-process success cannot substitute for external-process checks.

### P04 — Connect chat, durable tasks and native execution

**Owner:** R; C integrates harness/chat; reviewer V. **Prerequisites:** P01 and
P03, with P00 schemas. Target records from P02 may remain static-only.

**Work:** implement broker tables, atomic claims, leases, dispatch receipts,
feature states and generation-bound cancellation. Register typed tools in the
existing operator path. Add desktop-only internal RPCs and minimal chat progress,
evidence and Stop controls. Preserve plan/auto/full harness semantics. Route game
requests and follow-ups without detaching the task when knowledge is mentioned.
Preserve chosen provider and do not add silent provider/billing fallback.

**Outputs:** a full chat-to-native fixture run, durable operation history,
recovery tests and minimal UI status.

**Expected outcome:** one chat request performs inspect/apply/verify through real
tools and remains understandable after a failure or app restart.

**Pass checks:** `P04.chat_flow` original cooldown wording selects the game path
and carries course/target context; `P04.races` concurrent claims, duplicate
completions and stale revisions have deterministic outcomes; `P04.stop` Stop
before dispatch, during scanning and during a write clears active work and
records the actual outcome; `P04.crash` crash after mutation before response
records `outcome_unknown` without replay; `P04.recovery` old helper exit is
established before takeover; `P04.routing_regression` ordinary website/chat
requests preserve existing behavior.

Include competing sessions for the same process in `P03.ownership` and forged
renderer completion, missing credentials and stale executor epochs in
`P04.races`. `P04.stop` covers successful cleanup, cleanup timeout, helper death
and process identity change without reopening ordinary work.

### P05 — Find values and store reusable pointer recipes

**Owner:** R; N supplies scan primitives; reviewer V. **Prerequisite:** P04.

**Work:** support exact/range/unknown-initial scans, changed/unchanged/increased/
decreased refinement, typed values and bounded byte-pattern search. Use 4 MiB
chunks, a 256 MiB resident scan budget and a 2 GiB temporary snapshot limit per
session. Checkpoint at 120 seconds; resumption must show coverage and skipped
regions. Never report a partial search as exhaustive.

Add pointer discovery with default depth 6, configurable maximum 12, and default
offset window ±4096 bytes. Persist module-relative or derived static-field roots,
explicit steps, fingerprints, immutable revisions and observations. Revalidate
at use time. Distinguish candidate, observed, restart-verified and stale states.
Resolve flat dotted-key versus nested offset data explicitly; reject conflicts.

Migrate legacy pointers transactionally after a SQLite backup-API backup. Keep
old IDs/evidence and mark absolute addresses process-bound. Repeated old
observations do not automatically become restart verification. Import/export
preserves provenance and cannot elevate status by assertion.

**Outputs:** scanner/resolver, migrations, mapping APIs, bounded storage cleanup
and `scripts/verify_game_profile_restarts.py`.

**Expected outcome:** the app can narrow a value, save how to find it again, and
tell the user whether reuse was actually tested.

**Pass checks:** `P05.scans` all scan/refinement modes match independent fixture
ground truth, including boundaries and partial regions; `P05.restarts` the same
saved recipe resolves across three fresh processes without newly supplied
addresses; `P05.staleness` changed build/invalid pointer rejects use;
`P05.migration` backup, upgrade, repeated upgrade and rollback preserve records;
`P05.budgets` memory/disk limits, cancellation and cleanup work under a large scan.

### P06 — Deliver the first real feature: local Among Us no-cooldown

**Owner:** T; R integrates feature lifecycle; reviewer V. **Prerequisites:** P02
target memory access, P04 and P05. Record Mac and Windows separately.

**Work:** finish R3/R4 on each exact build. Derive and validate the local-player
resolver and controlling field/method; remove assumed offsets and silent default
values from the execution path. Observe a normal cooldown baseline. Apply the
selected local feature, require acknowledgement/readback, and separately check
whether the game accepts the intended action. Reacquire objects after round
changes; stop writes on process, round or local-player identity loss.

Freeze a reproducible scenario in the profile: local session mode, host/client
role, player role, map, match settings, participants/fixtures, action sequence
and timing method. Use the same relevant settings for baseline, enabled and
disabled trials. Save timestamps of accepted actions and the evidence used to
distinguish accepted actions from button readiness. Any changed scenario requires
a new comparable baseline before acceptance.

Disable by ending the override and applying the researched cleanup procedure.
For a countdown, do not blindly restore a stale captured timer. If normal
behavior requires a round restart, record that as the cleanup procedure and test
it. Add `scripts/verify_game_feature.py` to orchestrate and collect evidence;
human-observed gameplay may be a recorded step when no trustworthy automatic
oracle exists.

**Outputs:** exact-build feature profile, baseline/enabled/disabled recording,
read/write receipts, cleanup result and three-restart report.

**Expected outcome:** the user's first request visibly works in the selected
local Among Us mode, or the app identifies the exact failed stage.

**Pass checks:** `P06.baseline` normal cooldown prevents the repeated action;
`P06.readback` intended live object/field changes; `P06.behavior` the repeated
action is accepted earlier than baseline; `P06.disable` normal behavior returns
after the documented cleanup; `P06.lifecycle` round restart/death/disconnect/
process exit do not leave writes aimed at stale objects; `P06.restarts` the saved
resolver succeeds across three fresh processes. A ready button or zero value
without accepted gameplay fails `P06.behavior`.

### P07 — Add desktop boxes, lines and available game fields

**Owner:** U; T owns entity/camera qualification; reviewer V. **Prerequisites:**
P04/P05 and P06's first live-target acceptance milestone.

**Work:** replace the OpenCV preview as the product overlay with a transparent
Tauri window following the target viewport. Separate acquisition, validation,
projection and drawing. Implement boxes/lines and available fields, DPI/multiple
display/resize behavior, local-player filtering, null-field display and lifecycle
cleanup. Among Us exposes alive/dead status distinctly; numerical health support
requires a suitable target with a verified health field.

Default acquisition target is 30 updates/second. Hide target drawings after
500 ms without a valid frame. Record acquisition-to-render latency on one clock
where possible, otherwise report separate stages without false clock subtraction.

**Outputs:** replay fixtures, renderer, exact-target entity/camera report,
correlated screenshots/frame captures and 30-minute performance report.

**Expected outcome:** overlay content tracks measured game entities and disappears
when its data becomes invalid.

**Pass checks:** `P07.projection` independent known-point tests verify projection,
clipping and DPI; `P07.live_alignment` controlled target positions/events align
with rendered entities; `P07.truthful_fields` missing health/team remain unknown;
`P07.lifecycle` resize/minimize/exit/reconnect and stale frames behave correctly;
`P07.performance` a 30-minute run meets 30 updates/second within 10% on the named
test machine, with p95 receiver-to-render below 100 ms and memory growth below
10% after a five-minute warmup. These are acceptance targets, not current results.

### P08 — Resolve non-root BGMI feasibility on the actual phone

**Owner:** T and U, with non-overlapping claims; reviewer V. **Prerequisite for
live experiments:** P06 accepted on one desktop platform. Device inventory and
contract design can be prepared earlier.

**Work:** execute R6 as four ordered gates: package/runtime qualification, actual
entity/camera/field access, on-phone drawing, then integrated telemetry. Record
device model, OS/API, ABI, page size, package/version, signing identity and tool
pins. Qualify the selected instrumented-test-package approach before investing
in full feature development. Keep the original installation/data recoverable;
do not silently uninstall it to solve a signing conflict.

Persist `requested_features` before qualification: entity boxes, enemy lines and
numerical enemy health, including the intended entity visibility coverage.
Record each as verified, blocked or not applicable with its reason. Missing
requested data cannot be silently removed from the acceptance set. Among Us
alive/dead semantics do not establish BGMI health availability.

Capture non-root device state and the precise connector/access path used by each
experiment. Rooted/Zygisk evidence cannot qualify this lane. Produce a versioned
device budget before P09: target 30 updates/second within 10%, p95 local
receiver-to-render below 100 ms, memory growth below 10% after five-minute
warmup, and a 30-minute measured run. Record test resolution/entity load and
thermal state; fail if throttling prevents those limits or triggers an OS thermal
shutdown. A different budget requires an explicit plan revision before testing,
not a lower threshold chosen after seeing results.

**Outputs:** reproducible feasibility report, installation/startup diagnostics,
field availability, on-phone graphics proof, fixed mobile tool manifest and
cleanup steps, requested-feature matrix and device-budget record. Each unavailable
field is named explicitly.

**Expected outcome:** a supported/blocked result for full on-phone memory ESP on
that device/build, with a concrete reason for each gate.

**Pass checks:** `P08.launch` selected package reaches the intended test state;
`P08.data` controlled events validate real entities/camera and all requested fields;
`P08.phone_render` drawing appears on the phone with correct rotation/scaling/
touch; `P08.non_root` records that the successful path uses the stipulated non-root
configuration; `P08.budget` freezes the complete device budget;
`P08.feasibility_report` includes every result and a reproducible next
experiment for unresolved issues. A complete report may conclude blocked;
P09's full-feature work is eligible only when launch, data, phone rendering,
non-root and budget gates actually pass. Independent contract/renderer work can
continue without claiming full-feature eligibility.

### P09 — Integrate the experimental on-phone BGMI feature

**Owner:** U for phone runtime, T for data profile, R for session integration;
reviewer V. **Prerequisites:** P08 launch/data/phone-render gates passed and the
`desktop_controller_ready` milestone: P04 accepted on one Mac or Windows lane.
Record that controller OS/build in the phone integration evidence.
Also require the P02 phone qualification and target-memory-access receipt, plus
P08 non-root and device-budget gates.

**Work:** implement the qualified in-process connector and on-phone renderer with
the same identity/validity contract. Keep real-time frame work local to the
phone; desktop chat controls configuration and collects diagnostics. Replace
legacy mixed-message assumptions with exact framed reads, bounded lengths,
matching responses and one coherent entity/camera snapshot. Revalidate memory
ranges/object lifetime instead of copying unchecked legacy scanner code.

Implement requested boxes/lines/verified health, orientation, touch handling,
pause/resume, background/foreground, disconnect and cleanup. Unsupported fields
remain disabled and keep the corresponding requested feature blocked; a partial
implementation cannot pass the full requested feature set. Retain an explicit
experimental label for the exact build.

**Outputs:** pinned reproducible phone build, exact-build profile, install/run/
restore instructions, real device evidence and session diagnostics.

**Expected outcome:** real validated data drives the overlay on the non-rooted
phone over repeated sessions.

**Pass checks:** `P09.live_feature` actual entities drive on-phone boxes/lines and
every requested field in the fixed feature matrix; `P09.lifecycle` three launches plus rotation/background/
disconnect clear stale data within 500 ms; `P09.performance` a 30-minute device
run records frame timing, memory and thermal observations and meets the qualified
device budget fixed in P08; `P09.restore` connector/rendering stop and the tested
installation cleanup succeeds. Computer previews, fixtures and screenshots alone
cannot satisfy the live data gate.

### P10 — Package, migrate and verify release candidates

**Owner:** C with V. **Prerequisites:** P01–P07 for the desktop release; P08/P09
for advertising the mobile feature. A desktop release need not wait for an
unresolved mobile experiment, but the full requested scope remains incomplete.

**Work:** package the helper/resources for each OS without assuming global
Python, Unix paths or a fixed temporary port. Preserve existing backend owner
checks and sidecar freshness detection. Add Windows packaging/CI and device
checks where required. Verify migration using a copied real-format database and
SQLite-consistent backup. Update documentation and capability display so legacy
runbooks cannot masquerade as current verified compatibility.

**Outputs:** Mac/Windows release candidates, dependency/resource manifests,
upgrade/rollback report, support matrix and `scripts/validate_windows_release.py`.

**Expected outcome:** installing the app on a clean supported machine reproduces
the verified workflow without developer-environment dependencies.

**Pass checks:** `P10.regression` relevant backend, routing, TypeScript and Rust
checks pass; `P10.clean_install` packaged chat/helper/Stop flow works on clean
Mac and Windows environments; `P10.upgrade` existing sources/mappings survive
upgrade and rollback is exercised on the test copy; `P10.target_replay` decisive
live target checks pass against the packaged build; `P10.support_matrix` every
advertised capability links to evidence for its exact OS/game/build.

Conditional `P10.mobile_integration` is required before advertising mobile support
or accepting full scope: the packaged desktop controllers start/configure/stop
the qualified phone feature, collect its evidence and complete cleanup without
developer paths. Test each advertised desktop-controller OS against the qualified
phone profile. It is not required for a clearly identified desktop-only release.

### P11 — Keep mappings and knowledge usable after updates

**Owner:** R and K; C integrates; reviewer V. **Prerequisite:** P10 desktop
release. Mobile coverage is conditional on P09 becoming qualified.

**Work:** detect binary/metadata/source changes, mark dependent profiles stale,
rerun bounded qualification and compare findings. Keep last verified versions
and failure history. Promote an executed procedure only with citations and live
evidence. Export useful verified examples for the existing training pipeline;
fine-tuning remains a separate evaluated task. Evaluate alternative supported
access methods through experiments with measured reliability; do not infer
lower detection/ban risk from a short successful run.

**Outputs:** update invalidation workflow, requalification reports, procedure
promotion rules and versioned compatibility history.

**Expected outcome:** updates produce a clear "needs rechecking" state and an
evidence-based recovery path.

**Pass checks:** `P11.build_change` altered binaries invalidate dependent recipes
before execution; `P11.source_change` corrected lessons invalidate affected
procedures; `P11.requalification` new evidence attaches to a new revision without
rewriting history; `P11.recovery` a known compatible previous app/profile can be
restored in the test environment without asserting new-build compatibility.

### Parallel schedule

After P00, run P01, P02 and P03 in parallel. Then integrate P04 and P05. P06 is
the first game milestone. P07 desktop work and P08 mobile qualification can run
in parallel after that milestone; P09 depends on proven mobile feasibility.
Packaging preparation can happen earlier, but P10 release checks use integrated
artifacts. P11 follows the first accepted desktop release.

A phase can have `verified` Mac checks and `blocked` Windows checks. Preserve
both. Dependent platform-independent work can continue; claiming complete
Mac+Windows support requires both lanes. In the manifest, dependency milestones
distinguish "first desktop target passed" from "all platform checks passed".

## 6. Verification records, acceptance and resumption

### Evidence location and report fields

Store runtime evidence under:

```text
<app_data>/game-verification/game-runtime-v1/<phase_id>/<run_id>/
  report.json
  commands.jsonl
  manifest.json
  logs/
  captures/
  timings.csv
```

Commit schemas, fixture inputs and test definitions. Keep local target binaries,
raw memory captures, personal paths and device identifiers out of committed
fixtures. Evidence exports apply the existing redaction rules.

Each report contains plan/contract revisions, phase/check IDs, implementation
commit or patch hash, input artifacts, environment/tool versions, target/build/
process identity, commands with working directories and exit codes, expected
and observed results, artifact hashes, timings, cleanup outcome, blockers and
next action. Acceptance also records reviewer identity, reviewed revision and
the independently replayed decisive check.

Implementation statuses: `not_started`, `working`, `ready_for_review`, `verified`,
`failed`, `blocked`. Target statuses: `not_run`, `static_only`,
`runtime_access_blocked`, `runtime_ready`, `feature_verified`, `stale`.
Individual check results: `pass`, `fail`, `blocked`, `not_run`.

Changing code/contracts/target inputs invalidates affected acceptance reports.
Use input hashes and declared dependencies to identify what must be rerun;
unrelated edits do not require repeating the entire suite.

### Coordinator acceptance procedure

1. Verify the reported revision, target fingerprint and prerequisite artifacts.
2. Inspect changed files for ownership/contract violations and incomplete paths.
3. Validate report shape, required check IDs and artifact hashes.
4. Reproduce the decisive positive case and at least one relevant negative case
   independently on the named environment.
5. Check cleanup, cancellation and absence of leftover helpers/overlays.
6. Publish accepted checks, blocked checks, handoff and the next eligible phase.

Evidence must match the claim: self-process is not external-process access;
fixture success is not game success; memory readback is not gameplay success;
one session is not restart stability; renderer success is not entity accuracy;
Mac is not Windows; a computer overlay is not a phone overlay. A report validator
can check integrity and completeness, but it cannot replace live observation.

### Commands and acceptance tooling

Existing regression commands, run from the stated directory:

```sh
# backend/
uv run pytest -q

# repository root
npm run test:routing
npm run build
cargo test --locked --manifest-path src-tauri/Cargo.toml
python3 -m unittest discover -s tests/mac_mcp
```

The following are interfaces to implement in the named phases, not commands
claimed to exist today:

```text
P00: verify_game_plan.py --manifest <path> --reports <directory> --phase <id>
P02: qualify_game_target.py --target <record> --report-dir <directory>
P05: verify_game_profile_restarts.py --profile <record> --restarts 3 --report-dir <directory>
P06: verify_game_feature.py --profile <record> --feature no_cooldown --report-dir <directory>
P10: validate_windows_release.py --app <installed-path> --report-dir <directory>
```

The P00 verifier returns 0 only when required checks/dependencies pass, 1 for
failed/invalid/stale evidence, and 2 for blocked/not-run required checks. It must
print a simple reason and next action. Qualification distinguishes a successful
report-generation operation from a successful runtime capability check.

When another agent resumes: read this plan and the manifest, inspect team status,
find the latest accepted report for its platform, verify input hashes, and claim
the next eligible work item. Resume from the earliest failing prerequisite;
do not rerun a completed inspection without a changed input or unresolved fact.

The final full-scope acceptance requires the course-to-action workflow, reusable
mapping verification, actual Among Us behavior, desktop overlays, the qualified
non-root on-phone BGMI feature, and packaged regression/cleanup evidence. Until
all those gates pass, report the completed capabilities and remaining blockers
individually.
