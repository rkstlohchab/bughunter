# BugHunterClaudex — Native AX App Control & Offline Game Lab

## What's actually new

- Added `src-tauri/resources/axbridge.swift`, an Accessibility (AXUIElement) helper for standard macOS applications. It reads the foreground application's accessibility tree, can select exact named controls, and refuses ambiguous or protected fields. It does not expose a shell or remote-control server.
- Added `scripts/build_mac_axbridge.sh`, a local Swift compiler/installer for the helper. `python3 scripts/setup.py` now calls this on macOS after dependencies are installed. The binary is installed to `~/.local/share/bughunterclaudex/bin/axbridge` and is not downloaded.
- Updated Tauri's desktop action handler to use the native helper for read / button / tab / list / field-focus operations when installed. It retains the existing AppleScript fallback when the helper is not installed, and retains native session expiry, app-focus checks, Stop, read-only/control approval, and task evidence.
- Added `examples/offline_memory_lab.py`, a disposable test process with an explicitly known 32-bit money value.
- Added `scripts/verify_external_memory_lab.py` to test the *actual external-process* Mach memory reader on macOS. Optional `--write-lab` writes only to the disposable lab process, verifies the value inside the target process, then restores it.
- Added `scripts/verify_next_mac_control.sh` to compile the AX helper, run frontend/Rust source checks, and attempt a real external-process read test on macOS.
- Added `backend/tests/test_mac_axbridge_contract.py` to check the bridge contract and lab lifecycle.

## macOS setup

1. Extract the ZIP. Install Apple Command Line Tools (if necessary): `xcode-select --install`.
2. In the `bughunter/` folder, run `python3 scripts/setup.py`.
3. Run `npm run desktop:dev`.
4. In System Settings > Privacy & Security > Accessibility, approve the desktop application and **the separately compiled `axbridge` helper if macOS asks**. macOS permissions cannot be silently granted.
5. Chat > Control a Mac app > Choose an app > Test app access. Try a simple text-editor app before Burp or a game.
6. `bash scripts/verify_next_mac_control.sh` runs source checks and a lab *read* test on Mac.
7. Optional test that actually modifies only the disposable lab value: `python3 scripts/verify_external_memory_lab.py --write-lab`.

## Limitations and safety

- **This is not unrestricted Mac control.** The operator selects a running native application and approves a limited session. This cannot bypass macOS privacy settings, locked devices, app sandboxes, anti-cheat, code-signing, or System Integrity Protection.
- AXUIElement works only for controls exposed to macOS Accessibility. Custom-rendered games, inaccessible windows, and many emulated Windows apps might not expose usable controls. The existing macOS agent retains AppleScript actions for some menus, keys and scrolling.
- The AX helper is a separate process. It may require its own Accessibility authorization, even if the Tauri app has permission. A missing authorization causes a clear error instead of silently succeeding.
- Game-memory operations are already implemented in the existing bridge as approved scans/reads/writes, and can fail when `task_for_pid` or memory protection denies access. The new lab gives a reproducible way to verify those operations on one program we own. Arbitrary games/online services are not guaranteed compatible.
- The installer and GUI must be tested on a real Mac with Xcode, npm dependencies, and Rust. Neither Apple frameworks nor the Tauri Rust build were compiled in the Linux authoring environment.
- Keep application/game targets within an authorized scope. No stealth, credential access, kernel bypass, or anti-cheat evasion is provided.

## Checks in the authoring environment

- Python compileall: passed.
- Swift parser (`swiftc -frontend -parse`): passed. **Not macOS type checking or linking.**
- Shell syntax (`bash -n`): passed.
- Focused Python regression suite: 28 passed.
- Full macOS app build / external-process live test: unverified; see commands above.
