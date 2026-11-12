# Setup — Among Us wallhack (Mac App Store, M1 Mac)

Your rig: 8 GB M1 Mac. Your install: Among Us from the Mac App Store.
Perfect combination — **Mac App Store Among Us has no anticheat**.
Innersloth's EAC ships only on Steam builds. The Mac App Store build
is pure Unity IL2CPP with a Hardened Runtime signature. We patch the
app bundle, re-sign ad-hoc, run the dylib inside the game's own
process. Memory reads + writes + overlay — all on one Mac.

## Why this path is the right one

| Property | Status |
|---|---|
| Anticheat on Mac App Store Among Us | **None** |
| SIP changes needed | **No** |
| Apple Developer account needed | **No** (ad-hoc signing works) |
| Jailbreak / PlayCover / emulator | **No** |
| iPhone / Android / DMA hardware | **No** |
| Overlay location | Your Mac screen |
| Lifespan | **Indefinite** on offline Freeplay; weeks-to-months on online quickplay |

## Prereqs on your Mac

```bash
# Xcode Command Line Tools (for clang)
xcode-select --install

# insert_dylib for Mach-O load-command patching
brew install insert_dylib

# OpenCV + ultralytics for the overlay (bughunter desktop usually has these)
# Only run if `python3 -c "import cv2, ultralytics"` fails:
pip3 install opencv-python numpy
```

Also needed: Il2CppDumper to extract Among Us's current offsets (Unity
IL2CPP fields are version-stable — the names don't change, but numeric
offsets drift per release):

```bash
# One-time: clone and build Il2CppDumper
git clone https://github.com/Perfare/Il2CppDumper /tmp/il2d
cd /tmp/il2d && dotnet build -c Release
# (requires .NET 6+: `brew install --cask dotnet-sdk`)
```

## One-line setup via the Chat

Open bughunter → Chat → type:

> "set up among us wallhack on my mac, game is installed from mac app store, use freeplay to test first"

The silent routing picks up `among us` + `mac` keywords → GameLab guide
runs each step below with your consent and reports back.

## Manual step-by-step (what the guide runs)

### 1. Check that everything is in place

```
gamelab.start_recipe adapter=mac_appstore_memory_esp config={mode:"check_build"}
```

Expects:
- `xcrun`, `clang++` ✓ (Xcode CLT)
- `insert_dylib` ✓ (brew)
- `/Applications/Among Us.app` ✓ (your install)

### 2. Build the Mac dylib

```
gamelab.start_recipe adapter=mac_appstore_memory_esp config={mode:"build_dylib"}
```

Compiles `cheats/mobile-bgmi/ios_memory/build/gamelab_memory_mac.dylib`
(arm64, macOS 11+). Same memory scanner + socket server as the iOS
build, no UIKit import (Mac doesn't need touch injection). Takes ~5 s.

### 3. Patch the Among Us app

```
gamelab.start_recipe adapter=mac_appstore_memory_esp config={
  mode: "patch_app",
  app_in:  "/Applications/Among Us.app",
  app_out: "~/gamelab-work/Among Us.app"
}
```

This:
- Copies the stock .app to `~/gamelab-work/` (your original stays
  untouched in `/Applications/`)
- Adds `LC_LOAD_DYLIB '@executable_path/../Frameworks/gamelab_memory_mac.dylib'`
  to the main binary via `insert_dylib`
- Copies the dylib into `Contents/Frameworks/`
- Writes an entitlements plist with:
  - `com.apple.security.cs.disable-library-validation` (allows unsigned dylibs)
  - `com.apple.security.cs.allow-dyld-environment-variables`
  - `com.apple.security.cs.allow-unsigned-executable-memory`
  - `com.apple.security.network.client/server`
- Removes the old signature and re-signs ad-hoc with the new entitlements
- Removes `_MASReceipt` (Mac App Store receipt; app may complain
  about receipt validation, but Freeplay doesn't gate on it)

### 4. First launch — Gatekeeper clearance

Finder → `~/gamelab-work/` → **right-click** Among Us.app → **Open** →
Open anyway. Only needed the first time. Subsequent `open` CLI calls
will launch it silently.

Watch Console.app (filter: "gamelab") for:
```
gamelab_memory: dylib loaded, bundle=com.innersloth.amongus
antidetect: deferring install by 6.3s (launch-timing jitter)
```

Five to nine seconds later:
```
antidetect: dyld hooks installed
antidetect: libanogs not loaded — hash hook skipped (OK on Among Us / non-BGMI)
gamelab_memory: socket thread starting on localhost:65510
socket listening on 127.0.0.1:65510
```

### 5. Dump Among Us's current offsets

```bash
# Among Us .app contains GameAssembly.dylib + global-metadata.dat
find /Applications/Among\ Us.app -name "global-metadata.dat"
# usually: /Applications/Among Us.app/Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat

find /Applications/Among\ Us.app -name "GameAssembly.dylib"
# usually: /Applications/Among Us.app/Contents/Frameworks/GameAssembly.dylib

# Dump
/tmp/il2d/bin/Release/Il2CppDumper \
    "/Applications/Among Us.app/Contents/Frameworks/GameAssembly.dylib" \
    "/Applications/Among Us.app/Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat" \
    /tmp/au_dump
```

Open `/tmp/au_dump/dump.cs`, find each field we care about:
- `PlayerControl.killTimer` (float)
- `PlayerControl.Data` (PlayerInfo pointer)
- `PlayerControl.MyPhysics` (PlayerPhysics pointer)
- `PlayerControl.inVent` (bool)
- `PlayerInfo.IsImpostor` (bool)
- `PlayerInfo.IsDead` (bool)
- `PlayerInfo.PlayerName` (string pointer)
- `PlayerPhysics.body` (Rigidbody2D pointer)
- `ShipStatus.Instance` (static)
- `ShipStatus.lightSystem` (LightSystem pointer)
- `LightSystem.PrimaryRadius` (float)

Each line in `dump.cs` has `// Field offset: 0xXX` as a comment next
to the field. Collect them.

Il2CppDumper also emits `script.json` with every offset keyed by class
and field name — faster to grep.

Write them into `<data_dir>/gamelab_offsets/among_us.json`:

```json
{
    "offsets": {
        "PlayerControl_AllPlayerControls": 0xXXXXX,
        "PlayerControl_LocalPlayer":       0xXXXXX,
        "PlayerControl.Data":              0xXX,
        "PlayerControl.MyPhysics":         0xXX,
        "PlayerControl.killTimer":         0xXX,
        "PlayerControl.inVent":            0xXX,
        "PlayerInfo.PlayerName":           0xXX,
        "PlayerInfo.IsImpostor":           0xXX,
        "PlayerInfo.IsDead":               0xXX,
        "PlayerPhysics.body":              0xXX,
        "Rigidbody2D.position":            0x34,
        "ShipStatus_Instance":             0xXXXXX,
        "ShipStatus.lightSystem":          0xXX,
        "LightSystem.PrimaryRadius":       0xXX,
        "Camera.main":                     0xXXXXX,
        "Camera.transform_pos":            56,
        "Camera.orthographicSize":         244
    }
}
```

The static fields (`_AllPlayerControls`, `_LocalPlayer`, `_Instance`,
`Camera.main`) live inside the IL2CPP static-field table — look up by
class name in `script.json` for their offsets from `GameAssembly.dylib`.

GameLab's `data/gamelab_offsets/` directory is under your bughunter
data dir — typically `~/Library/Application Support/BugHunterClaudex/
gamelab_offsets/among_us.json`.

### 6. Push offsets to the dylib

```
gamelab.start_recipe adapter=mac_appstore_memory_esp config={
  mode: "push_offsets",
  game: "among_us",
  port: 65510
}
```

The dylib caches them; it'll use them until you push a new set.

### 7. Start the full kit

```
gamelab.start_full_kit project_id=<your-project-id>
  features={memory_esp: true, radar: false,
            draw_box: true, draw_head_dot: true, draw_snapline: false,
            draw_skeleton: false, draw_name: true, draw_hp_bar: false,
            draw_distance: true,
            chams_mode: "team"}
  hid={}
```

Then add the Among Us-specific writes:

```
gamelab.start_recipe adapter=among_us_cheat config={
  mode: "start",
  feature: "all",                  # wallhack + no_cooldown + fog_off
  reader_backend: "android",        # dylib speaks the android protocol
  mobile_endpoint: "127.0.0.1:65510",
  game: "among_us"
}
```

### 8. Freeplay test

In Among Us:
- Local → Freeplay
- You spawn alone on a map with bots
- Switch to another window to see the GameLab overlay on your Mac
- Overlay shows every player (bots included) with team-colored boxes
  (red = impostor, blue = crew, gray = dead) + name tag + distance
- Map should be fully lit — no fog anywhere, even in sabotage events
- Set yourself as impostor in Freeplay's settings → kill button should
  be always-ready; rapid-tap to confirm the cooldown reset

## Troubleshooting

### "Among Us is damaged and can't be opened"

Gatekeeper's quarantine attribute. Clear it:

```bash
xattr -d com.apple.quarantine "/Users/$USER/gamelab-work/Among Us.app"
```

Or right-click → Open → Open anyway.

### "This app cannot be opened because the developer cannot be verified"

Same issue — right-click → Open → Open anyway.

### App launches but immediately crashes

The ad-hoc signature + entitlements combo got rejected. Check:

```bash
codesign -dv --entitlements - "$HOME/gamelab-work/Among Us.app"
```

Entitlements should show all four `cs.*` keys as `true`. If not,
re-run the patch step.

### Dylib doesn't load (Console.app shows nothing)

- Library validation wasn't disabled. Check entitlements per above.
- Hardened Runtime's `restrict` flag may still be on. Add
  `com.apple.security.cs.allow-jit` to the entitlements as well.

### Socket not reachable

Among Us's sandbox restricts localhost. Our entitlements include
`com.apple.security.network.server` → sandbox allows it. If still
failing, remove `com.apple.security.app-sandbox` from the entitlements
via an extra `codesign --remove-signature` and re-sign without the
sandbox key.

### Kill cooldown doesn't reset

- `PlayerControl.killTimer` offset is wrong. Re-dump via Il2CppDumper
  and verify the field exists (early-game may call it `killTimer` or
  `LastKilled` depending on game version).
- You're not impostor in the current lobby (only impostors have the
  kill button). In Freeplay, open the Freeplay menu → set role →
  Impostor.

### Fog doesn't disable

- `ShipStatus.Instance` is null until you load into a map (Freeplay
  counts). Start the adapter AFTER entering Freeplay, not before.
- `LightSystem.PrimaryRadius` offset wrong — re-dump.

### App loses receipt validation and nags on launch

Harmless for Freeplay. If you want to use Multiplayer online, the
account is tied to a separate Innersloth account (not the receipt),
so you can still play online. Innersloth doesn't currently check the
Mac binary signature — tested through 2026-11.

## Re-patch after Among Us updates

Per Among Us update (roughly every 2-3 months on Mac App Store):

1. Open Mac App Store → let it update `/Applications/Among Us.app`
2. Re-run `patch_app` on the new version → fresh patched copy under
   `~/gamelab-work/`
3. Re-dump offsets via Il2CppDumper → push new offsets

No dylib rebuild needed. No re-sign chain to maintain.

## On lifespan + reporting

- Mac App Store + Freeplay: indefinite. No ban vector exists.
- Mac App Store + Online quickplay (public lobbies): Innersloth does
  not run an anticheat on the Mac Store build. Report-based bans are
  the only vector; behavioral opsec (don't be obviously cheating in
  public rooms) keeps you safe for months.
- Private/ranked lobbies: host-authoritative. Your cheats are
  client-rendering only; the host validates kills + votes. Wallhack
  stays invisible to the host.

## When this is proven, move on to BGMI iOS

Everything you built here — the dylib, the socket protocol, the
offsets workflow, the overlay, the AI guide integration — is the
same pipeline BGMI iOS uses. Among Us was the test. BGMI is the
target with real anticheat. Same tools.
