---
title: BGMI ESP — scrcpy + YOLO vision, mobile or external
tags: [bgmi, pubg-mobile, esp, mobile, scrcpy, yolo]
source: in-house
updated: 2026-11-12
lifespan_class: invisible-external, weeks-on-device
---

# BGMI ESP

Three paths, by anticheat risk and quality.

## Path A — External: scrcpy + YOLO on PC (recommended for ranked)

The phone runs stock BGMI. scrcpy mirrors the phone's screen over USB to a PC. YOLO runs on the PC and draws boxes on the mirror. The phone sees nothing.

**Pros:** invisible to ACE / anogs on the phone. No root required. Works on brand-new phones with locked bootloaders.

**Cons:** ~60-100ms screen-to-draw latency. Sensor fingerprint still identifies you as a connected-controller tier player — matched against similar peers.

**Setup:**

```bash
# On PC (Linux/Mac/Windows):
# Install scrcpy and adb
# macOS: brew install scrcpy android-platform-tools
# Linux: apt install scrcpy android-tools-adb
# Windows: download scrcpy from https://github.com/Genymobile/scrcpy

# On phone:
# Enable developer options → USB debugging

# Plug phone to PC via USB, authorize the computer.

adb devices                       # confirms phone visible
scrcpy --video-codec=h265 --max-fps=60 --no-audio --video-bit-rate=20M
```

Then run the BGMI ESP pipeline from `cheats/mobile-bgmi/bgmi_esp.py`:

- Grabs frames from scrcpy's output (via OBS Virtual Camera or direct scrcpy-v4l2 bind on Linux)
- Runs YOLOv8n trained on BGMI enemy sprites
- Draws boxes on a window on the PC
- Optional: if you want aimbot, send mouse-like events back via ADB's `input` command (slow — ~50ms extra)

## Path B — External: OTG mouse + mobile YOLO + Arduino

For aimbot on mobile. Use a phone with USB-OTG, connect:

```
Phone USB-C → OTG hub → Arduino (presents as mouse) + USB keyboard
```

The Arduino receives aim deltas over a Bluetooth channel from a nearby PC, re-emits as HID mouse to the phone. BGMI accepts mouse at the "connected device" matchmaking tier. The gaming phone sees a vanilla mouse.

**Matchmaking caveat.** Connected-device tier means you're matched against other mouse/controller players. Easier opponents. If you need pure-touch tier, this path doesn't apply.

## Path C — On-device: rooted + GameGuardian / native hook

For single-player TDM / warmup / bot lobbies, or short-session use.

See `stacks/mobile-root.md` for setup. ESP via GameGuardian hex scripts or a Zygisk native module. Lifespan weeks between anogs updates.

## YOLO training data

For Path A and Path B, you need a YOLOv8n model trained on BGMI enemy sprites. Collection:

1. Record 30+ minutes of BGMI gameplay (TDM warmup or custom room gives a controlled training environment).
2. Extract frames: `ffmpeg -i gameplay.mp4 -vf fps=5 frames/%05d.png` — ≈ 2000 frames per 30 minutes.
3. Label in Roboflow (free tier) or CVAT. Draw box around each enemy character.
4. Export YOLO format.
5. Train on Kaggle (free T4 x2):

```bash
yolo detect train \
    data=bgmi.yaml \
    model=yolov8n.pt \
    epochs=150 \
    imgsz=640 \
    batch=32 \
    device=0,1
```

GameLab has `gamelab.capture_dataset` to automate frame extraction and Roboflow upload prep, and `kaggle.launch_training` to push the training job.

6. Export best checkpoint `best.pt` → rename to `bgmi_v8n.pt` → drop under `cheats/mobile-bgmi/weights/`.

## Expected mAP

A reasonable training set (1500-3000 labeled frames, varied maps + skins + distance) gets ~0.80 mAP@50 on BGMI enemy detection. That's plenty for ESP; aimbot wants ~0.90 for consistent lock.

## FOV + ROI

BGMI screen on a 6" phone is ≈ 2400×1080. For ESP, process the full frame. For aimbot, process a 640×640 center ROI — enemies get the "head" as top 22% of bbox.

## Rendering overlay on scrcpy mirror

The scrcpy window is OpenGL-backed. On Linux you can hook into it directly; cross-platform, use OBS Virtual Camera as a tee + draw a separate ImGui window at the same resolution with transparent alpha + display topmost.

In GameLab, the simpler choice is a side-by-side PyQt window: left pane = scrcpy video, right pane = detection boxes with same coords. Low effort, works everywhere.

## Aim/trigger behavior on mobile (Path B)

Aim deltas on a 90Hz phone → max corrections ≤ 15/sec. Touch-mode players see your crosshair slide. Humanize hard:

- Bezier curves
- Reaction floor 180ms (phone players are slower than mouse players — be slower)
- Miss rate 10-12%

## Current lifespan

- Path A (external scrcpy): invisible to the game. Lifespan = your BGMI account's lifespan.
- Path B (OTG + Arduino): invisible to the game. Matchmaking tier is the trade.
- Path C (on-device): 1-4 weeks between anogs updates.

## Reference

- `cheats/mobile-bgmi/bgmi_esp.py` — full external pipeline
- `cheats/mobile-bgmi/scrcpy_launcher.sh` — one-shot start
- `cheats/mobile-bgmi/magisk_hide/module.prop` — Magisk module stub for Path C
