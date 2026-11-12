# Setup — External vision stack

For Valorant / CS2 / BGMI (external path) / Apex / Rust / Fortnite — any
game. Zero software footprint on the gaming PC.

## Parts list

- HDMI splitter (passive is fine for 1080p; powered for 4K/120Hz)
- Capture card (MS2130 cheap, Elgato 4K60 Pro premium)
- Second PC with a GPU: GTX 1660 Super+ for NVIDIA, M1+ for Apple MLX
- Arduino Leonardo or Pro Micro (ATmega32u4 required for native USB HID)
- USB-A data cable from Arduino to the gaming PC
- Short USB-C cable from Arduino to the second PC (programming + comms)

Total parts floor: ~$80 (MS2130 $20, Pro Micro $7, HDMI splitter $10,
cables).

## Step 1 — physical wiring

```
Gaming PC HDMI out ──> HDMI splitter ──+──> monitor
                                       └──> capture card ──USB──> Second PC

Arduino Pro Micro ──USB-A──> Gaming PC  (appears as HID mouse)
Arduino Pro Micro ──USB-C──> Second PC  (serial comms from vision_aim.py)
```

If your gaming PC has DisplayPort out only, use a DP→HDMI active adapter
before the splitter.

## Step 2 — second-PC software

```bash
# Linux:
sudo apt install python3-pip python3-venv v4l-utils arduino-cli ffmpeg
# macOS:
brew install python@3.11 arduino-cli ffmpeg

python3 -m venv ~/.gamelab && source ~/.gamelab/bin/activate
pip install ultralytics opencv-python pyserial numpy
# NVIDIA extras (one of):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
# Apple Silicon extras:
pip install mlx mlx-lm
```

## Step 3 — verify capture card

Linux:

```bash
v4l2-ctl --list-devices
# Expect /dev/video0 bound to your capture device
ffplay /dev/video0     # should show the gaming PC's screen
```

macOS: open QuickTime Player → New Movie Recording → source dropdown
should list the capture card.

Windows: OBS Studio → add Video Capture Device source.

## Step 4 — flash the Arduino

```bash
arduino-cli core update-index
arduino-cli core install arduino:avr

# Identify port
arduino-cli board list
# e.g. /dev/ttyACM0 or /dev/cu.usbmodem14201

cd cheats/external-vision/arduino
arduino-cli compile --fqbn arduino:avr:leonardo .
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:leonardo .
```

Or use GameLab:

```
gamelab.start_recipe adapter=arduino_hid mode=flash
  config={port:"/dev/ttyACM0", board_fqbn:"arduino:avr:leonardo"}
```

## Step 5 — verify Arduino ping

```bash
python3 - <<'EOF'
import serial, struct, time
s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
time.sleep(1.8)
s.write(struct.pack("<BB", 0xCC, 0x55))
print("resp:", s.read(2).hex())
EOF
# expect: cd 55
```

Or:
```
gamelab.start_recipe adapter=arduino_hid mode=probe config={port:"/dev/ttyACM0"}
```

## Step 6 — collect training frames

Record ~30 minutes of the target game at 60 fps. For BGMI/mobile, the
scrcpy mirror is your source.

```bash
# From a video file:
ffmpeg -i gameplay.mp4 -vf fps=5 dataset/images/%05d.jpg

# Live capture via the vision_esp adapter:
gamelab.capture_dataset project_id=<id> source=/dev/video0 duration_s=1800 fps=5
```

Label in Roboflow (free tier — https://app.roboflow.com) or CVAT. One
class: `enemy`. 1500-3000 labeled frames target.

Export in YOLO format → unpack to `dataset/`:
```
dataset/
  data.yaml
  images/{train,val}/
  labels/{train,val}/
```

## Step 7 — train

Local (GTX 1660+):

```bash
cd cheats/external-vision
python3 train_model.py --data dataset/data.yaml \
    --model yolov8n.pt --epochs 150 --batch 32 \
    --name valorant --out weights/valorant_v8n.pt
```

Kaggle (free T4x2, 30h/week):

```python
# Kaggle notebook cell:
!pip install -q ultralytics
!yolo detect train data=/kaggle/input/valorant-dataset/data.yaml \
  model=yolov8n.pt epochs=150 imgsz=640 batch=32 device=0,1 \
  project=/kaggle/working/runs name=valorant
# Download /kaggle/working/runs/valorant/weights/best.pt
```

GameLab:
```
kaggle.push_corpus dir=cheats/external-vision/dataset
kaggle.launch_training notebook=valorant_v8n
# wait, then:
kaggle.pull_adapter run_id=<id>  # or .pt file via Kaggle UI
```

Drop `best.pt` into `cheats/external-vision/weights/valorant_v8n.pt`.

## Step 8 — first live run

ESP only (safest, no aim):

```
gamelab.start_recipe project_id=<id> adapter=vision_esp config={
  capture_index: 0,
  model_path: "cheats/external-vision/weights/valorant_v8n.pt",
  esp_overlay: true,
  aimbot_enabled: false
}
```

An OpenCV window opens on the second PC showing the capture with boxes
drawn on detected enemies. Watch it on your second monitor during play.

ESP + aim:

```
gamelab.start_recipe project_id=<id> adapter=vision_esp config={
  capture_index: 0,
  arduino_port: "/dev/ttyACM0",
  model_path: "cheats/external-vision/weights/valorant_v8n.pt",
  esp_overlay: true,
  aimbot_enabled: true,
  sens_scale: 2.6,
  tick_ms: 90,
  miss_rate: 0.08,
  reaction_floor_ms: 180
}
```

## Step 9 — tune

Watch for 10-15 minutes of play. Adjust in the config:

- Crosshair over-shoots targets → raise `aim_smooth` (0.35 → 0.5)
- Cursor jitters → raise `tick_ms` (90 → 120)
- Flick feels superhuman → raise `reaction_floor_ms` (180 → 220) and
  raise `miss_rate` (0.08 → 0.12)
- Cursor doesn't reach target → lower `sens_scale` incrementally

Save tuned values in the project. `gamelab.get_project` returns them.

## Step 10 — opsec floors

Before ranked:

```
gamelab.opsec_check project_id=<id> stats={
  hs_percent: 42, winrate: 60, pre_fire_ratio: 0.12,
  climb_per_week: 1, avg_reaction_ms: 195
}
```

Flags any floor crossed. Fix before playing.

## Troubleshooting

- **Capture card shows black** — splitter HDCP strip. Buy a HDCP-stripping
  splitter (Portta-style passive splitters usually strip).
- **Arduino moves cursor on wrong monitor** — Windows multi-mon offset.
  Reduce `sens_scale` for scaled DPI monitors.
- **YOLO latency >30ms** — drop `imgsz` to 320, swap YOLOv8n for 8n-q
  quantized, or upgrade to a RTX 3060+.
- **Vanguard "repaired game files on launch"** — unrelated to this stack
  (second PC isolated). Harmless.
