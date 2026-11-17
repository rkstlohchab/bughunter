# Combat Master: Claude live-test handoff

2026-11-16. The operator has installed the game and requests testing through computer control with minimal manual work. They will supervise. Start with offline gameplay and one ammunition change, then verify disable and pointer persistence. Continue existing P05/P06 work; do not mark Among Us acceptance complete from this different game's results.

## Confirmed observations

- Steam app 2281730, installed build **24397779**, bundle version **0.30.4**.
- Bundle: `/Users/raksithlochabb/Library/Application Support/Steam/steamapps/common/Combat Master/CombatMaster.app`.
- Main executable: `Contents/MacOS/Combat Master`. Universal x86_64/arm64; the observed running image is **arm64**.
- Main SHA-256: `2f4834e1256802a728266998b7e627ec0056876d4882ccd111d917f449883c9d`.
- Observed PID **78927**, process birth **1794819155726612000**. These are historical observations; rediscover and verify the current process.
- **Native attachment succeeded using the existing `MacMemoryAdapter`, backend `lldb`.** A second attach read 32 bytes of the main executable at observed runtime VA `0x1008d8000`. Bytes start `cffaedfe0c000001`, confirming a 64-bit ARM Mach-O header. The sample hash is `e18b941e4f2b0b7eb4084c3f22ceb6edffd44a40649f89abc290f3817db24446`.
- SIP is **enabled**. It did not prevent this attachment/read. Direct Mach access was not the successful route; LLDB was. Do not diagnose the present game as attach-blocked or change SIP based on the Among Us failure.
- The adapters were closed after each probe. The process was subsequently observed alive in state `S`. **No game memory write or gameplay modification occurred.**

Evidence: `artifacts/game-verification/game-runtime-v1/combat-master-live-001/{attach-probe.json,runtime-read.json,install-observation.json}`. Module addresses in these files are observations from one process, not persistent pointers or offsets to ammunition.

## Current code and UI gaps

1. `backend/bughunter/game_runtime/targets.py` recognizes wrapped `Frameworks/UnityFramework.framework/UnityFramework`, but this build uses `Contents/Frameworks/UnityPlayer.dylib`, `Contents/Frameworks/GameAssembly.dylib`, and `Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat`. Discovery defaults to `/Applications`; the game is in Steam's library. Verification can consume explicit `extras.module_paths`, but the runtime probe still hard-codes UnityFramework. Inspect and extend all three paths consistently; preserve wrapped Among Us support and existing process birth/path/hash checks.
2. Fat Mach-O parsing currently chooses the first supported slice. That is x86_64 in this file, although this running instance was arm64. Select a valid static slice and confirm runtime architecture separately. Never equate first slice order with execution architecture.
3. `service.py:feature_start` currently requires Among Us names `local_mode`, `impostor_role`, `alive`, `round_identity` for every real game. Add an appropriate Combat Master offline profile with measured checks before claiming repeated-feature integration. Preserve Among Us checks for Among Us. No fake fixture classification or fabricated guards.
4. `agent-desktop` has Accessibility and Screen Recording granted. `list-windows` observed game windows `w-16762` (1440x900) and `w-16764` (1440x44, y=-44). A headless snapshot could not find the game window; a window screenshot failed. A whole-screen screenshot showed the IDE rather than the game. `launch com.AlfaBravo.CombatMaster --activate --headed` returned `AMBIGUOUS_TARGET`, delivered_unverified, because both windows were candidates. Re-observe, select the exact current game window, and verify focus/screenshots before input. Do not claim offline mode has been entered.

## Execute next

1. Read `docs/game-lab/P05-P06-completion.md` for implemented tools, lifecycle guarantees and pending acceptance. Inspect the evidence above; repeat a bounded read using `backend/bughunter/memory_engine/mac_adapter.py:MacMemoryAdapter` and close the adapter in `finally`.
2. Bring the exact game window forward with the installed `agent-desktop` CLI. Read its current help/skills. Enter offline mode using observed controls. Discover the PID automatically. Ask the operator only for an actual inaccessible action or OS prompt.
3. Extend target qualification for the measured Unity layout and Steam location. Hash main, UnityPlayer, GameAssembly, metadata and version-source files. Add focused conventional/fat-layout tests and retain wrapped regressions. Record unsupported parser outcomes honestly. An available pinned Cpp2IL executable is at `artifacts/game-verification/game-runtime-v1/codex-P00-P03-repair-001/tools/Cpp2IL-2022.1.0-pre-release.21-OSX-ARM64`; verify its hash before use. Its compatibility with this game has not been tested.
4. Observe ammunition before and after firing/reloading. Use bounded read-only scans/refinements and/or measured IL2CPP layouts to establish the actual local-player ammunition field. Record type, ownership evidence, process identity and build. Do not guess offsets or treat a UI value match as sufficient identity.
5. In the confirmed offline session, perform one conditional write with readback. Observe actual firing behavior, then disable/clean up and confirm normal behavior. Use existing durable app execution for the final integration test; label any standalone diagnostic separately. If a repeated feature is needed, first implement the target-specific checks above and verify Stop/session change/app-close behavior.
6. Save the measured mapping with provenance. Revalidate across three fresh launches before calling it restart-verified. Report separately: attachment, memory read, candidate discovery, write/readback, gameplay effect, disable, restarts and app-chat execution. A passed header read proves only read access, not gameplay compatibility or write success.

Store logs, screenshots, receipts and mappings under the Combat Master evidence directory, with hashes. Preserve historical P00-P06 reports and obtain fresh independent acceptance for changed source. Leave unrelated dirty files untouched; do not commit unless requested.

## State when handed off

Codex and its GPT-6.1 Sol target-layout worker made **no implementation edits**. The worker stopped without launching tests. Only this handoff and the diagnostic evidence were added. Existing untracked `tests/mac_mcp/test_ownership.py` and `tests/ui/game-runtime.spec.ts` belong to earlier work and were left untouched. No source tests were rerun during this diagnostic pass.

REA tools were absent from this session. Its suggested read-only doctor command failed because `rea-agents@6.9.0` requires a newer supported Node range than installed Node `v26.7.0`. No REA/client configuration or runtime was changed. The observations above came from ordinary shell tools and the existing memory adapter, not REA Evidence.
