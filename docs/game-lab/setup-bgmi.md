# Setup — BGMI (and COD-Mobile / Free Fire)

Three paths. Pick one based on your anti-ban appetite.

## Path A — External: scrcpy + PC YOLO (recommended)

Invisible to BGMI. No root. Works on any phone with USB-debug.

### Parts

- Phone with BGMI installed
- USB-C data cable (not power-only)
- PC with GPU (NVIDIA or Apple Silicon)
- (optional) OTG hub + Arduino Pro Micro + USB keyboard for mouse-tier
  aim injection

### Steps

```bash
# PC side (macOS example):
brew install scrcpy android-platform-tools
python3 -m venv ~/.gamelab && source ~/.gamelab/bin/activate
pip install ultralytics opencv-python numpy

# Phone side:
# Settings → About → tap Build number 7x → developer options unlocked
# Developer options → USB debugging → ON

# Plug phone to PC, authorize when prompted.
adb devices                       # expect one line with "device" state

# Verify safety profile:
gamelab.start_recipe adapter=bgmi_mobile config={mode:"safety_check"}
# Flags any issues (build fingerprint, debuggable, root visible).

# Start the pipeline:
gamelab.start_recipe project_id=<id> adapter=bgmi_mobile config={
  mode: "start",
  max_fps: 60,
  bit_rate: "20M",
  overlay: true,
  aim_via: "none"
}
```

A scrcpy window opens with the phone's screen mirrored. A separate
`GameLab-BGMI-xxxx — overlay` window shows detection boxes drawn on top.

### Training weights

Bootstrap with the shipped generic model, then fine-tune on your own
capture. Collect training frames inside the project:

```
gamelab.capture_dataset project_id=<id> source="GameLab-BGMI-xxxx" \
   duration_s=1800 fps=5
```

Label + train per `setup-external-vision.md` step 7. Export weights to
`cheats/mobile-bgmi/weights/bgmi_v8n.pt`.

### If you want aim

Three options for sending input back to the phone:

| Via | Latency | Tier |
|---|---|---|
| ADB `input tap` | 60-120ms | pure-touch (matched with phone players) |
| Arduino HID mouse through OTG hub | 15-30ms | connected-device tier (mouse players) |
| On-device touch injector (requires root) | 10-15ms | pure-touch |

ADB taps are slower but keep you in the pure-touch tier. For aimbot you
typically want the faster path — OTG + Arduino. Set
`aim_via: "arduino"` + `arduino_port: "/dev/ttyACM0"`.

## Path B — External: OTG mouse direct injection

Skips scrcpy. Phone runs stock. Arduino HID is plugged via OTG hub into
the phone itself. BGMI accepts mouse at the connected-device matchmaking
tier.

### Wiring

```
Phone USB-C
   |
   v
OTG hub ──> Arduino Pro Micro (HID mouse profile)
         └─> USB keyboard (satisfies connected-controller detection)
```

The Arduino receives aim deltas from a nearby PC over Bluetooth or
WiFi. On the PC, run the vision_aim pipeline with a capture-card source
pointing at the phone's HDMI-out (if your phone has HDMI-out / Alt Mode)
or at scrcpy mirror.

Trade-off: matched against other mouse/controller players, easier
opponents.

## Path C — On-device: rooted + GameGuardian/Zygisk

Only for short-session use. Lifespan weeks between anogs updates.

### Steps

1. Root with Magisk (device-specific — Pixel line is easiest).
2. Install modules:
   - Zygisk-enabled Magisk ≥ 26.1
   - **Shamiko** (Zygisk DenyList hider)
   - **MagiskHide Props Config**
   - **safetynet-fix**
   - **PlayIntegrityFix**
   - GameLab Hide module (`cheats/mobile-bgmi/magisk_hide/`)
3. In Magisk Settings → Zygisk ON → Enforce DenyList ON.
4. Add `com.pubg.imobile` to DenyList (Shamiko hides it).
5. Pick build fingerprint profile matching your device's class — Pixel 7
   profile ships in `cheats/mobile-bgmi/magisk_hide/system.prop`.
6. Reboot.
7. Verify:

```bash
gamelab.start_recipe adapter=bgmi_mobile config={mode:"safety_check"}
# all flags must be empty
```

8. Launch cheat tooling (GameGuardian, LSPosed module, or your own
   Zygisk native .so).

Reset the setup every 2-4 weeks as anogs updates.

## Opsec

See `prompts/game-knowledge/opsec/behavioral.md`. BGMI-specific:

- Reaction floor: 220ms (phone players are slower than mouse)
- Miss rate: 0.10-0.15 (higher than PC)
- Headshot ratio: cap at 42% for current rank
- Don't hot-drop every match
- Don't stream the cheating account

## Troubleshooting

- **`adb devices` lists `unauthorized`** — unplug, re-plug, accept the
  USB-debug prompt on the phone.
- **scrcpy says "server launch error"** — scrcpy-server failed to push.
  Try `--video-codec h264`.
- **`libanogs.so` detection flag after safety-check pass** — anogs pushes
  new hide-bypasses every few weeks. Check the XDA Magisk thread for
  current workarounds.
- **BGMI immediately kicks on launch** — Play Integrity failing. Reinstall
  PlayIntegrityFix module, reboot.
