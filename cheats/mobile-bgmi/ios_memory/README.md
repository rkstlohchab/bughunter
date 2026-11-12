# BGMI iOS memory ESP — non-jailbroken path

The one honest technique that works on a non-jailbroken iPhone: **patch
your own legitimately-installed BGMI IPA with an ESP dylib, re-sign
with your Apple ID, sideload via AltStore.** The dylib loads inside the
game process on launch, walks UE4 memory, ships entity snapshots to
your Mac over `usbmuxd` (iProxy forward — iOS equivalent of
`adb forward`). The PC-side universal overlay draws on your Mac.

This is how every working "non-jailbreak iOS cheat" does it. There is no
other way on non-jailbroken iOS — Apple's sandbox blocks sibling-process
memory reads at the kernel level. `task_for_pid` requires entitlements
Apple only grants its own signed tools.

## What you bring

- iPhone with BGMI installed (iOS 14+)
- Your Mac (M1 / M2 — iProxy and AltServer both run native on Apple
  Silicon)
- Apple ID. Free tier is fine; you'll re-sign weekly. Paid Apple
  Developer ($99/year) = re-sign yearly, cleaner, recommended.
- **Your own BGMI IPA**, extracted from your paired iPhone with
  **iMazing** (free trial will work) or **3uTools** on Mac. Do not
  download random BGMI IPAs from Telegram/Discord "cheat stores" —
  they're resigned with soon-to-be-revoked enterprise certs AND
  typically carry additional malware.

## How it works

```
┌───────────────────────────────────────────────┐
│ iPhone (sandboxed, non-jailbroken)            │
│                                                │
│   BGMI.ipa (patched + re-signed)               │
│   ├─ Original BGMI binary                      │
│   ├─ gamelab_memory.dylib  ← injected          │
│   │    - localhost socket server :65510        │
│   │    - UE4 entity-list walker                │
│   │    - Touch event injector (aimbot)         │
│   └─ Entitlements.plist (your dev cert)        │
│                                                │
└───────────────┬───────────────────────────────┘
                │ USB
                ▼
        usbmuxd on your Mac
                │
                │ iproxy -u <udid> 65510 65510
                ▼
┌───────────────────────────────────────────────┐
│ Mac — your bughunter desktop                  │
│                                                │
│   ios_memory_esp adapter                       │
│     ↓                                          │
│   memory_esp reader loop (reader_backend=ios)  │
│     ↓                                          │
│   Universal ESP overlay (OpenCV window)        │
│                                                │
└───────────────────────────────────────────────┘
```

## Step-by-step setup

### 1. Extract your BGMI IPA

Install **iMazing** on your Mac (free trial works). Plug in iPhone, pair,
trust. iMazing → Manage Apps → BGMI → right-click → **Extract IPA**.
Save as `BGMI-stock.ipa`.

Alternative: **3uTools** (free, Windows + Mac via Rosetta) has
"Extract IPA" that works on any paired iPhone.

### 2. Build the GameLab iOS dylib

Open Terminal on your Mac:

```bash
cd cheats/mobile-bgmi/ios_memory
bash scripts/build_dylib.sh
# Produces: build/gamelab_memory.dylib (arm64)
```

Requires: Xcode Command Line Tools (`xcode-select --install`).

### 3. Patch the IPA

```bash
bash scripts/patch_ipa.sh \
    --ipa ~/Downloads/BGMI-stock.ipa \
    --dylib build/gamelab_memory.dylib \
    --out BGMI-gamelab.ipa
```

This:
- Unzips the .ipa
- Adds `LC_LOAD_DYLIB` pointing to `@executable_path/gamelab_memory.dylib`
- Copies the dylib into the app's `Frameworks/` directory
- Patches the Info.plist entitlements to allow localhost sockets
- Rezips

### 4. Re-sign for your Apple ID

Install **AltServer** on your Mac: https://altstore.io

```bash
# Launch AltServer from the menu bar
# Plug in iPhone, keep paired + trusted
# On the iPhone: Settings → Privacy → Developer Mode → enable
# Reboot the iPhone (iOS 16+ only)

# AltStore app installs on the iPhone (first-time flow)
```

Then:

```bash
bash scripts/sideload.sh --ipa BGMI-gamelab.ipa --via altstore
```

This hands the patched IPA to AltStore via the paired Mac. AltStore
re-signs with your Apple ID and installs on the iPhone.

**Free Apple ID**: valid for 7 days, then re-sign. AltStore has a
background refresh that handles it if your Mac is powered and on the
same network — set and forget.

**Paid Developer ID**: valid for 365 days per sign. Zero weekly
hassle.

### 5. First launch

Open the patched BGMI on your iPhone. The dylib loads during the
app-launch Mach-O load phase. It starts a localhost TCP server on port
65510 inside the sandbox.

Verify the dylib loaded:

```bash
# Still have the iPhone paired to the Mac
bash scripts/health_check.sh
# Expected: "gamelab_memory.dylib loaded in BGMI (pid=<n>)"
#           "localhost:65510 reachable via usbmuxd"
```

### 6. Forward the socket to your Mac

```bash
bash scripts/iproxy_forward.sh --udid <iphone-udid> --port 65510
# Backgrounds: iproxy -u <udid> 65510 65510
```

Now `127.0.0.1:65510` on your Mac talks to the dylib inside BGMI on
your iPhone, over USB.

### 7. Push offsets + start the ESP overlay

In the bughunter Chat panel (first nav), type:

> "start BGMI iOS memory ESP on my iphone, udid is <paste from `idevice_id -l`>"

The silent routing picks it up, routes to the GameLab guide, the guide
runs:

- `gamelab.start_recipe adapter=ios_memory_esp mode=start_bridge` with your UDID
- Pushes `bgmi.offsets.json` to the dylib via the socket
- Spawns the universal memory-ESP reader + overlay
- The overlay window opens on your Mac showing boxes, bones, HP, names
  of every enemy BGMI is rendering

### 8. Aimbot (same dylib, different mode)

The dylib includes an in-process touch-event injector. The PC-side
combat loop sends aim deltas; the dylib converts them to synthetic
touch events delivered to UE4's input handler inside the process.
Zero external hardware. No MFi gamepad spoofing needed.

In the Chat:

> "enable aim on bgmi ios, 220ms reaction, 12% miss"

Routes to the combat adapter with `hid_backend=ios_dylib`,
`endpoint=127.0.0.1:65510`.

## Honest lifespan

Days to a few weeks before an account ban. Here's what can detect you,
in order of fastest:

1. **Krafton account-level flag.** anogs on iOS hashes the game bundle.
   Any modification to the IPA changes the hash. Krafton's server
   matches it against the known-good set and silently flags.
   **Mitigation:** we keep the original Info.plist bundle identifier
   and version; still detectable, but takes Krafton 1-3 days typically.
2. **In-process anogs hook scan.** `libanogs.A.dylib` (bundled with
   BGMI iOS) walks the loaded dylibs via `dyld_image_count()` +
   `_dyld_get_image_name()`. Our dylib appears in that list under its
   bundle path. **Mitigation:** the dylib renames itself in the Mach-O
   `install_name` field to something innocuous
   (`UIKit.framework/UIKit`-adjacent). 1-2 weeks between anogs
   updates.
3. **Cert revocation.** If dj's Apple Developer cert gets revoked (or
   the free-tier 7-day signing expires without AltStore refresh), the
   patched BGMI refuses to launch. Different failure mode: no ban,
   just app-doesn't-open. Re-sign fixes it.
4. **Modified-bundle fingerprint on login.** Krafton's login check
   sends the running bundle hash. Ours is different. They may put you
   in an "integrity failed" queue → silent matchmaking downgrade
   (bot-lobby queue), not a visible flag.

If you top-frag ranked lobbies with this, expect a 3-7 day account life.
If you play TDM/warmup only and keep stats within human distribution,
2-4 weeks is realistic before Krafton's bundle-hash sweep catches up.

## What this is NOT

- **Not** reading memory from a SIBLING app. Apple blocks that
  absolutely on non-jailbroken iOS. The dylib is inside BGMI's own
  process, sharing its address space.
- **Not** a store-bought IPA (iOSGods, Panda, Tutu). Those run on
  stolen enterprise certs that die every 1-7 days and ship with
  additional malware. Yours is legit-signed with your own Apple ID.
- **Not** a cloud cheat. Everything runs on your iPhone + your Mac.
- **Not** permanent. Nothing is. Plan to re-patch when BGMI updates
  or when anogs pushes a new hide-bypass.

## Files in this directory

| File | Role |
|---|---|
| `src/main.mm` | `__attribute__((constructor))` entry — spawns the socket thread on dylib load |
| `src/socket_server.mm` | Localhost TCP server on port 65510 inside the sandbox |
| `src/memory_scanner.mm` | UE4 GWorld walker — same algorithm as Android Zygisk, different runtime bindings |
| `src/touch_injector.mm` | In-process touch-event injector for aimbot |
| `src/common.h` | Shared types |
| `scripts/build_dylib.sh` | clang/ld build pipeline |
| `scripts/patch_ipa.sh` | IPA unzip → LC_LOAD_DYLIB insert → repack |
| `scripts/sideload.sh` | AltStore / Sideloadly handoff |
| `scripts/iproxy_forward.sh` | usbmuxd socket forward |
| `scripts/health_check.sh` | Verify dylib loaded + socket reachable |

## Legal & operational notes

- This modifies a binary you legitimately installed. You're not
  distributing it, you're running a patched copy for yourself.
- Account ban is on Krafton's side, not Apple's. Apple itself doesn't
  care about game cheating — they only enforce their code-signing +
  sandbox, which this fully respects.
- Don't resell, share, or redistribute the patched IPA. That's where
  the enterprise-cert cheat shops get themselves (and their customers)
  killed.
