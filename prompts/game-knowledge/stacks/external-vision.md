---
title: External vision + HID stack (capture card + YOLO + Arduino)
tags: [external, vision, yolo, arduino, hid, universal]
source: in-house
updated: 2026-11-12
lifespan_class: invisible
---

# External vision + HID — the universal stack

Zero code on the gaming PC. The gaming PC's HDMI goes to a splitter: one output to the monitor, one to a capture card on a second PC. The second PC runs YOLO on the captured frames, computes an aim delta, and sends it over USB serial to an Arduino Leonardo/Pro Micro. The Arduino enumerates on the gaming PC as a standard HID mouse and moves the cursor.

The anticheat on the gaming PC sees a vanilla USB mouse and a vanilla HDMI output. Nothing to detect in software.

## Hardware

| Part | Options | Notes |
|---|---|---|
| HDMI splitter | Any passive splitter; Elgato HDMI Splitter | Powered splitters better for 4K/120Hz |
| Capture card | Elgato 4K60 Pro, Avermedia Live Gamer 4K, MS2130 USB dongle (cheap 1080p60) | <5ms latency matters; MS2130 is ~$20 and good enough for 1080p60 |
| Second PC | GTX 1660 Super or better (YOLOv8n @ 320x320 @ ≥ 100 fps); mac M1+ works with MLX export | Can be a laptop |
| HID microcontroller | Arduino Leonardo, Pro Micro (ATmega32u4); Teensy 4.0/4.1 (faster) | Native USB HID required; Uno won't work |
| USB cable | USB data cable from μC to gaming PC | Not a power-only cable |

Optional upgrades:
- Elgato Stream Deck for runtime toggles (sens, FOV, miss-rate) via USB HID buttons
- Monitor with daisy-chain DP out instead of splitter

## Software

On the vision host (second PC):

- Python 3.11+
- `opencv-python` (video capture + ROI crop)
- `ultralytics` (YOLOv8 inference)
- `pyserial` (talk to Arduino)
- `numpy`
- CUDA (if NVIDIA) or MLX (if Apple Silicon)

On the gaming PC:
- Nothing. This is the whole point.

On the Arduino:
- Arduino IDE + the built-in `Mouse.h` library (built into Leonardo/Pro Micro core)

## Pipeline

```
HDMI splitter → capture card → second PC
                                  │
                                  v
                         frame grab (OpenCV)
                                  │
                                  v
                    ROI crop (center 320×320 of 1080p)
                                  │
                                  v
                       YOLOv8n inference @ GPU
                                  │
                                  v
               target selection (nearest-to-crosshair + conf)
                                  │
                                  v
                    head-offset + humanization (bezier + miss)
                                  │
                                  v
                     int16 dx, dy over USB serial
                                  │
                                  v
                 Arduino → HID mouse report → gaming PC
```

## Detection surface

Anticheat sees:
- One HID mouse (whitelisted USB VID/PID — both Leonardo and Pro Micro use VID 0x2341)
- Standard HDMI output from the game

Anticheat does not see:
- The frame being analyzed (it's on another PC)
- The vision model
- The decision logic
- Any process, driver, file, or memory read

Detection paths that remain:
- **Behavioral ML.** Flick curves, reaction times, pre-fire ratios, HS%. Humanization is mandatory — see `opsec/behavioral.md`.
- **Screenshot comparison.** AC takes screenshots of the game. External vision doesn't add anything to those screenshots, so this path misses entirely.
- **Input-device fingerprinting.** Arduino VID/PID is in every mouse vendor database; a vendor-specific `HID_REPORT_DESCRIPTOR` that doesn't match any known mouse can be a weak flag. The stock Leonardo descriptor is fine; custom reports should mimic a known mouse (e.g. Logitech G Pro: VID 0x046D, PID 0xC54C — flash with a custom descriptor if paranoid).

## Latency budget

Typical end-to-end latency (enemy pixel-visible → crosshair moves):

| Stage | Time |
|---|---|
| Game render → HDMI out | 16ms (one frame @ 60Hz) |
| HDMI through splitter | <1ms |
| Capture card buffer | 5-15ms (MS2130 ≈ 10ms; Elgato ≈ 5ms) |
| Frame grab + ROI | 2ms |
| YOLO inference (320×320 on 1660S) | 8-12ms |
| Serial write | 1ms |
| Arduino → HID report | <1ms |
| Gaming PC USB polling + render | 1-16ms (depends on polling rate 1000Hz = 1ms) |
| **Total** | **40-60ms** |

40-60ms total is faster than most humans' voluntary reaction time (150ms+), so you can afford humanization delay.

## Humanization (mandatory)

See `opsec/behavioral.md`. Floors:

- Min 150ms between enemy-visible and move
- Bezier curve (quadratic ease-in-out), not linear
- Miss rate ≥ 0.05 (random offset applied to 5-8% of corrections)
- Max corrections/sec ≤ 11 (90ms tick floor)
- Deadzone at target (stop correcting when dx, dy < 3)

## Reference build

Full runnable in `cheats/external-vision/`:
- `vision_aim.py` — pipeline
- `esp_overlay.py` — second-screen ESP renderer (draw boxes/names on a window on the second PC)
- `train_model.py` — YOLO training on captured frames
- `arduino/hid_mouse.ino` — Arduino firmware

## Setup runbook

See `docs/game-lab/setup-external-vision.md`. The GameLab UI panel walks through it with hardware auto-detection.
