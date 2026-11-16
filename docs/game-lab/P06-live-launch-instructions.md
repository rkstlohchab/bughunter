# P06 live verification status

Updated by Codex during the implementation review. The previous instructions overstated completion: the standalone verifier is not connected to the app's feature controls, and a plausible countdown candidate is not proof of LocalPlayer identity.

## What is currently measured

Claude's `P06/run-002/logs/P06_live_attach_attempt.log` records a failed attach to PID 18851: `task_for_pid` returned 5 and LLDB reported `Not allowed to attach to process`. This proves attach was denied in that run. It does not establish that re-signing, PlayCover, elevated execution, or a system configuration change will fix it. No such change is part of this review.

The current standalone command discovers the process automatically, measures that running executable, rejects an unqualified build, and reports a blocked attach in `summary.json`. It does not ask the operator to find a PID. With no saved profile, it only records diagnostic candidates; it never picks a plausible float and writes to it.

## Agent procedure

1. Read `P05-P06-codex-review.md` and its latest test records. Do not reuse historical author reports as acceptance of changed sources.
2. Check the running process and exact build. Keep an attach denial recorded as blocked. Desktop control does not confer native memory access.
3. Establish a measured module-relative LocalPlayer resolver, including the intermediate class/static-fields pointers. Offsets `0x0` and `0xB0` alone do not establish that root. Save and observe that recipe against its exact build. Do not encode an arbitrary heap address as a module RVA.
4. Finish the P05/P06 chat-to-native integration. Currently game chat has durable read/write/Stop, while scans, pointer discovery and feature lifecycle remain standalone library/CLI APIs. A separate CLI process is not governed by app Stop and does not produce app activity receipts.
5. Once both integration and live access work, use the existing computer-control tools to configure and operate the local scenario while the operator supervises. Discover process IDs automatically. Use the task's Computer control grant and Foreground input setting for keyboard/mouse actions. Ask the operator only for a required OS prompt or a demonstrated inaccessible action.
6. Record normal cooldown, actual accepted repeated kills with the feature enabled, natural behavior after disabling, object/round changes and process exit. Timer reads and button appearance alone do not prove accepted kills. Do not fill the operator's human attestation yourself.
7. Reuse the same saved recipe across three fresh game processes without rediscovering addresses. The restart CLI accepts a launcher that explicitly emits `bh-runtime-ready <pid>`; `open -W` does not implement that protocol and is not a valid example launcher.

## Standalone diagnostic commands for the coding agent

Use the backend environment and fresh report directories. `--fixture` runs a disposable process, never Among Us, and has a separate verdict.

```sh
backend/.venv/bin/python scripts/verify_game_feature.py --fixture --duration-s 1 --report-dir /tmp/bughunter-feature-review-unique
backend/.venv/bin/python scripts/verify_game_profile_restarts.py --fixture --report-dir /tmp/bughunter-restart-review-unique
backend/.venv/bin/python scripts/verify_game_feature.py --report-dir /tmp/bughunter-live-diagnostic-unique
```

A live run with no profile is read-only diagnostic discovery. A controlled live feature test requires `--profile <saved-observed-recipe-id>`. Stop a standalone test with its own process interrupt; do not advertise the app's Stop button for it. Even a completed live capture returns `pending_gameplay_review` (exit 2), not phase acceptance. Fixture success returns exit 0 but never sets `gameplay_verified`.
