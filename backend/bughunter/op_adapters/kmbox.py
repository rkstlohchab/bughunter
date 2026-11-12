"""kmbox — KMBox Net adapter. Discovers a KMBox on the LAN and talks to it.

KMBox B+ / B Pro speaks a simple JSON protocol over TCP on port 65432.
This adapter is for setup verification + one-shot testing. Continuous
mouse-delta injection during a cheat run is done by the reader process
(``cheats/dma-stack/pcileech_reader.py``), which opens its own socket.
"""
from __future__ import annotations

import json
import socket
import time

from ._common import envelope


def run(app, *, mode: str = "probe",
        endpoint: str | None = None,
        host: str | None = None, port: int = 65432,
        dx: int = 0, dy: int = 0,
        button: str = "left",
        **_) -> dict:
    if mode == "probe":
        return _probe(host=host, port=port, endpoint=endpoint)
    if mode == "move":
        return _move(host=host or _host_from_endpoint(endpoint),
                     port=port, dx=dx, dy=dy)
    if mode == "click":
        return _click(host=host or _host_from_endpoint(endpoint),
                      port=port, button=button)
    if mode == "discover":
        return _discover()
    raise ValueError(f"unknown mode: {mode}")


def _host_from_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    if ":" in endpoint:
        return endpoint.split(":", 1)[0]
    return endpoint


def _probe(*, host, port, endpoint) -> dict:
    h = host or _host_from_endpoint(endpoint)
    if not h:
        return envelope("kmbox",
                        {"command": "probe", "exit_code": -1,
                         "stdout": "", "stderr": "no host/endpoint",
                         "duration_ms": 0}, ok=False)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect((h, port))
        s.sendall(b'{"cmd":"status"}\n')
        data = s.recv(1024)
        s.close()
        body = data.decode("utf-8", errors="replace")
        return envelope("kmbox",
                        {"command": "status", "exit_code": 0,
                         "stdout": body, "stderr": "",
                         "duration_ms": 0}, ok=True,
                         endpoint=f"{h}:{port}")
    except Exception as exc:
        return envelope("kmbox",
                        {"command": "probe", "exit_code": -1,
                         "stdout": "", "stderr": str(exc),
                         "duration_ms": 0}, ok=False)


def _move(*, host, port, dx, dy) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    s.connect((host, port))
    s.sendall(json.dumps({"cmd": "mouse_move", "dx": int(dx),
                           "dy": int(dy)}).encode() + b"\n")
    data = s.recv(1024)
    s.close()
    return envelope("kmbox",
                    {"command": f"mouse_move {dx} {dy}", "exit_code": 0,
                     "stdout": data.decode(errors="replace"),
                     "stderr": "", "duration_ms": 0},
                    dx=dx, dy=dy)


def _click(*, host, port, button) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    s.connect((host, port))
    s.sendall(json.dumps({"cmd": "mouse_click",
                           "btn": button}).encode() + b"\n")
    data = s.recv(1024)
    s.close()
    return envelope("kmbox",
                    {"command": f"mouse_click {button}", "exit_code": 0,
                     "stdout": data.decode(errors="replace"),
                     "stderr": "", "duration_ms": 0}, button=button)


def _discover() -> dict:
    """Scan common LAN ranges for a KMBox on port 65432."""
    found: list[str] = []
    for octet in range(1, 255):
        for net in ("192.168.1.", "192.168.0.", "10.0.0."):
            addr = f"{net}{octet}"
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.05)
            try:
                s.connect((addr, 65432))
                found.append(addr)
            except Exception:
                pass
            finally:
                s.close()
    return envelope("kmbox",
                    {"command": "discover", "exit_code": 0,
                     "stdout": "", "stderr": "",
                     "duration_ms": 0}, found=found)
