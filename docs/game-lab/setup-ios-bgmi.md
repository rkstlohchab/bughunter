# Setup — BGMI (and other UE4 games) on non-jailbroken iOS

Memory ESP + in-process aimbot on a stock iPhone + your Mac. No
jailbreak, no HDMI capture card, no model training. The cheat is a
dylib injected into your legitimately-installed BGMI via a patched +
re-signed IPA, sideloaded through AltStore.

**Lifespan: days to a few weeks.** Krafton's bundle-hash check on their
servers will eventually flag the modified IPA. Budget accordingly.

## What you need

- iPhone with BGMI installed (iOS 14+)
- Your Mac (M1 or later works great)
- Apple ID. Free = re-sign weekly; paid Developer ($99/yr) = re-sign
  yearly.
- **Your own BGMI IPA**, extracted from your paired iPhone via iMazing
  (free trial, Mac) or 3uTools. Do not use random IPAs off cheat
  Telegram channels — they're re-signed with soon-dead enterprise certs
  and typically carry malware.

## One-command install

Once the tools are in place (see below), run this from the bughunter
Chat:

> "set up bgmi on my iphone, udid is ABCDEF-01234567, my ipa is at ~/Downloads/BGMI-stock.ipa"

The silent routing picks up `bgmi` + `iphone` keywords → GameLab guide
→ runs each step below with your consent + reports back.

## Manual step-by-step (what the guide runs under the hood)

### 1. Prereqs on your Mac

```bash
xcode-select --install                             # Xcode CLT (for clang)
brew install libimobiledevice insert_dylib         # iproxy + Mach-O patcher
# AltServer: install the GUI from https://altstore.io, open it,
# install AltStore on your iPhone through its menu-bar flow.
```

GameLab verifies with:
```
gamelab.start_recipe adapter=ios_memory_esp config={mode:"check_build"}
```

### 2. Extract your BGMI IPA

Install iMazing on your Mac. Plug iPhone in, pair, trust. In iMazing:
Manage Apps → BGMI → right-click → **Extract IPA** → save as
`~/Downloads/BGMI-stock.ipa`.

Alternative: 3uTools "Extract IPA" button.

### 3. Build the dylib

```
gamelab.start_recipe adapter=ios_memory_esp config={mode:"build_dylib"}
```

Compiles `cheats/mobile-bgmi/ios_memory/build/gamelab_memory.dylib`
(arm64, iOS 14+). Takes ~5 seconds.

### 4. Patch the IPA

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "patch_ipa",
  ipa_in:  "/Users/you/Downloads/BGMI-stock.ipa",
  ipa_out: "/Users/you/Downloads/BGMI-gamelab.ipa"
}
```

Inserts `LC_LOAD_DYLIB @executable_path/Frameworks/gamelab_memory.dylib`
into the Mach-O, copies the dylib into the app bundle's Frameworks/,
strips existing code signatures (AltStore will re-sign).

### 5. Sideload

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "sideload",
  ipa_out: "/Users/you/Downloads/BGMI-gamelab.ipa",
  via: "altstore"
}
```

Hands the IPA to AltServer, which re-signs with your Apple ID and
pushes to the paired iPhone. On the iPhone, the first-time install
will prompt: Settings → VPN & Device Management → trust your Apple
ID's certificate.

**Free Apple ID** — AltStore auto-refreshes every 7 days when your Mac
is powered + online. Set and forget.

**Paid Developer** — one re-sign per year.

### 6. iOS Developer Mode (iOS 16+)

On the iPhone: Settings → Privacy & Security → **Developer Mode** →
enable → reboot. Required for sideloaded apps on iOS 16+.

### 7. First launch

Open the patched BGMI on your iPhone. Watch Console.app on your Mac
(filter: "gamelab") — expect:

```
gamelab_memory: dylib loaded, bundle=com.tencent.ig
gamelab_memory: socket listening on 127.0.0.1:65510
```

### 8. Bridge the socket to your Mac

Find the iPhone's UDID:
```bash
idevice_id -l
```

Then from the Chat:

> "start bgmi ios memory esp, udid <paste>, project <name>"

Or explicitly:

```
gamelab.start_full_kit project_id=<id>
  features={memory_esp: true, radar: true, aim: false,
            draw_box: true, draw_head_dot: true, draw_snapline: true,
            draw_skeleton: true, draw_name: true, draw_hp_bar: true,
            draw_distance: true}
  hid={ios_udid: "<your udid>", ios_port: 65510}
```

Which calls under the hood:
- `ios_memory_esp mode=forward` — `iproxy -u <udid> 65510 65510`
- `ios_memory_esp mode=health_check` — pings the dylib
- `ios_memory_esp mode=push_offsets` — ships `bgmi_ios.offsets.json`
  into the dylib over the socket
- `memory_esp` reader loop with `reader_backend=android`
- Universal ESP overlay
- Optional radar + combat loop

### 9. First-session offset derivation

The shipped `cheats/game_data/bgmi_ios.offsets.template.json` has zeros
for every offset — BGMI iOS's layout differs per release and nobody
ships Krafton-current iOS offsets publicly. You'll either:

- Dump the live offsets yourself via an AOB scan of the running BGMI
  process on your iPhone (same `offset_finder.py` pattern, adapted to
  read from the dylib's `read` cmd), or
- Import from a community source (search "BGMI iOS offsets <version>"
  for the currently-live set — updated on UC and specialist Discord
  communities)

Once you have them in `<data_dir>/gamelab_offsets/bgmi_ios.json`,
restart the reader:

```
gamelab.start_recipe adapter=memory_esp config={
  mode: "restart_reader", reader_pid: <pid>
}
```

The overlay stays running — only the reader re-opens with fresh
offsets.

## Aimbot

In the full-kit features, flip `aim: true`. The combat adapter sends
aim deltas over the same socket; the dylib's touch injector converts
them into synthetic UITouch events delivered to BGMI's input layer.
Humanization defaults (reaction floor 220ms, miss rate 12%) match the
Android mobile defaults.

**TDM / unranked testing.** anogs runs the same scan in TDM, warmup,
ranked, and custom rooms. The anticheat doesn't care which mode you're
in — the bundle hash + loaded-image scan fires everywhere. Test in a
**custom room with friends** first (no reports from strangers), then
TDM for behavior tuning, then whatever mode you actually want.

## Troubleshooting

- **AltStore says "could not refresh" at the 7-day mark** → open
  AltStore on the iPhone, tap the refresh button. Keep your Mac on
  the same WiFi.
- **BGMI immediately crashes on launch** → the dylib didn't pass code
  signing. Re-run `sideload.sh`; if it still fails, your Apple ID has
  run out of free-tier sign slots (3 apps max). Remove one, retry.
- **anogs flags "integrity failed" on login** → Krafton's server sees
  the modified bundle. They'll silent-queue you into bot lobbies.
  Expected outcome; no way around short of patching the login handshake
  (per-season reverse engineering work, not shipped).
- **`idevice_id -l` shows nothing** → iPhone not paired. Settings →
  General → VPN & Device Management → check that your Mac is listed.
- **Health check fails but Console.app shows the dylib loaded** → an
  iOS firewall tweak is blocking localhost. Settings → Privacy →
  Local Network → make sure BGMI has localhost access.

## Files shipped

- `cheats/mobile-bgmi/ios_memory/src/{main,socket_server,memory_scanner,touch_injector}.mm` — the dylib
- `cheats/mobile-bgmi/ios_memory/scripts/build_dylib.sh` — clang build
- `cheats/mobile-bgmi/ios_memory/scripts/patch_ipa.sh` — IPA patcher
- `cheats/mobile-bgmi/ios_memory/scripts/sideload.sh` — AltStore handoff
- `cheats/mobile-bgmi/ios_memory/scripts/iproxy_forward.sh` — usbmuxd forward
- `cheats/mobile-bgmi/ios_memory/scripts/health_check.sh` — verify dylib loaded
- `backend/bughunter/op_adapters/ios_memory_esp.py` — PC-side adapter
- `cheats/game_data/bgmi_ios.offsets.template.json` — offset slot template
