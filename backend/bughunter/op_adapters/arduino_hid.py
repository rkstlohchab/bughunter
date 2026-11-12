"""arduino_hid — flash + probe + comms helper for the Arduino HID mouse.

Supports:
- ``list``   — enumerate connected Arduinos
- ``flash``  — compile and upload the hid_mouse sketch via arduino-cli
- ``probe``  — open serial, send a ping, verify ACK
- ``move``   — send one test move (open then send-and-close)

Long-running comms is owned by ``vision_esp`` (it writes to the same
serial port directly). This adapter is for setup + verification.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path

from ._common import envelope, locate, run_cmd


def run(app, *, mode: str = "list",
        port: str | None = None,
        baud: int = 115200,
        board_fqbn: str = "arduino:avr:leonardo",
        sketch_path: str | None = None,
        dx: int = 0, dy: int = 0,
        **_) -> dict:
    if mode == "list":
        return _list_ports()
    if mode == "flash":
        return _flash(port=port, baud=baud, board_fqbn=board_fqbn,
                      sketch_path=sketch_path)
    if mode == "probe":
        return _probe(port=port, baud=baud)
    if mode == "move":
        return _move(port=port, baud=baud, dx=dx, dy=dy)
    raise ValueError(f"unknown mode: {mode}")


def _list_ports() -> dict:
    try:
        import serial.tools.list_ports as lp
    except Exception as exc:
        return envelope("arduino_hid", {"command": "", "exit_code": -1,
                                         "stdout": "", "stderr": f"pyserial missing: {exc}",
                                         "duration_ms": 0}, ports=[])
    ports = []
    for info in lp.comports():
        ports.append({
            "device": info.device, "product": info.product or "",
            "manufacturer": info.manufacturer or "",
            "vid": info.vid, "pid": info.pid,
            "likely_arduino": info.vid in (0x2341, 0x2A03, 0x1B4F, 0x16C0),
        })
    return envelope("arduino_hid", {"command": "list", "exit_code": 0,
                                     "stdout": "", "stderr": "",
                                     "duration_ms": 0}, ports=ports)


def _flash(*, port, baud, board_fqbn, sketch_path) -> dict:
    if not port:
        raise ValueError("flash requires port")
    if not sketch_path:
        repo_root = Path(__file__).resolve().parents[3]
        sketch_path = str(repo_root / "cheats" / "external-vision" / "arduino" / "hid_mouse.ino")
    sketch = Path(sketch_path)
    if not sketch.exists():
        raise FileNotFoundError(f"sketch missing: {sketch_path}")
    sketch_dir = sketch.parent
    # arduino-cli produces a build dir alongside the sketch
    cli = locate("arduino-cli",
                 hint="brew install arduino-cli  (or https://arduino.github.io/arduino-cli/)")
    compile_res = run_cmd([cli, "compile", "--fqbn", board_fqbn,
                            "--export-binaries", str(sketch_dir)],
                           timeout=180)
    if compile_res["exit_code"] != 0:
        return envelope("arduino_hid", compile_res, step="compile")
    upload_res = run_cmd([cli, "upload", "-p", port, "--fqbn", board_fqbn,
                           str(sketch_dir)], timeout=180)
    return envelope("arduino_hid", upload_res, step="upload",
                     port=port, fqbn=board_fqbn, sketch=str(sketch))


def _probe(*, port, baud) -> dict:
    if not port:
        raise ValueError("probe requires port")
    try:
        import serial
    except Exception as exc:
        return envelope("arduino_hid", {"command": "", "exit_code": -1,
                                         "stdout": "", "stderr": f"pyserial: {exc}",
                                         "duration_ms": 0}, ok=False)
    try:
        ser = serial.Serial(port, baud, timeout=1.5)
        time.sleep(1.8)  # Leonardo reset on open
        ser.reset_input_buffer()
        ser.write(struct.pack("<BB", 0xCC, 0x55))   # PING
        resp = ser.read(2)
        ser.close()
        ok = (resp == b"\xCD\x55")
        return envelope("arduino_hid",
                        {"command": "ping", "exit_code": 0 if ok else 1,
                         "stdout": resp.hex(), "stderr": "",
                         "duration_ms": 0}, ok=ok, port=port)
    except Exception as exc:
        return envelope("arduino_hid",
                        {"command": "ping", "exit_code": -1,
                         "stdout": "", "stderr": str(exc),
                         "duration_ms": 0}, ok=False, port=port)


def _move(*, port, baud, dx, dy) -> dict:
    if not port:
        raise ValueError("move requires port")
    import serial
    ser = serial.Serial(port, baud, timeout=1.5)
    time.sleep(1.8)
    dx = max(-32767, min(32767, int(dx)))
    dy = max(-32767, min(32767, int(dy)))
    ser.write(struct.pack("<BhhB", 0xAA, dx, dy, 0x55))
    ser.close()
    return envelope("arduino_hid",
                    {"command": f"move {dx} {dy}", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    dx=dx, dy=dy, port=port)
