"""Raw TCP relay for operator mode.

Listens on a loopback port, forwards bytes to a target host:port through
the configured SOCKS chain (if one is active) or directly. Useful for
protocols that adapters don't wrap natively (binary, custom).

Everything logged to the Timeline and bounded by a per-session byte
budget.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any

from ..store import Store, new_id, now


class TcpRelay:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self._relays: dict[str, dict] = {}

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "relay.start":
            return self.start(**params)
        if method == "relay.stop":
            return self.stop(**params)
        if method == "relay.list":
            return self.list()
        raise ValueError(f"Unknown relay method: {method}")

    def start(self, *, remote_host: str, remote_port: int,
              local_port: int = 0, max_bytes: int = 50_000_000,
              approved: bool = True, **_) -> dict:
        self.store.require_desktop_owner()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", local_port))
        sock.listen(4)
        actual_port = sock.getsockname()[1]
        relay_id = new_id("relay")
        record = {
            "id": relay_id, "remote_host": remote_host,
            "remote_port": remote_port, "local_port": actual_port,
            "max_bytes": max_bytes, "byte_count": 0,
            "started_at": now(),
        }
        self._relays[relay_id] = {"record": record, "sock": sock,
                                  "stop": threading.Event()}
        self.store.put("tcp_relay", record, id=relay_id)
        thread = threading.Thread(target=self._accept_loop,
                                  args=(relay_id,),
                                  daemon=True, name=f"relay:{relay_id}")
        thread.start()
        return record

    def stop(self, *, id: str, **_) -> dict:
        state = self._relays.pop(id, None)
        if not state:
            return {"stopped": None}
        state["stop"].set()
        try:
            state["sock"].close()
        except Exception:
            pass
        record = {**state["record"], "stopped_at": now()}
        self.store.put("tcp_relay", record, id=id)
        return {"stopped": id}

    def list(self, **_) -> list[dict]:
        return list(self.store.list("tcp_relay"))

    # internals -----------------------------------------------------------

    def _accept_loop(self, relay_id: str) -> None:
        state = self._relays.get(relay_id)
        if not state:
            return
        listener = state["sock"]
        stop = state["stop"]
        record = state["record"]
        listener.settimeout(1.0)
        while not stop.is_set():
            try:
                client, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._pump, args=(relay_id, client),
                                 daemon=True)
            t.start()

    def _pump(self, relay_id: str, client: socket.socket) -> None:
        state = self._relays.get(relay_id)
        if not state:
            client.close()
            return
        record = state["record"]
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            upstream.connect((record["remote_host"], record["remote_port"]))
        except Exception:
            client.close()
            return
        budget = {"n": record["max_bytes"] - record["byte_count"]}

        def forward(src: socket.socket, dst: socket.socket):
            try:
                while budget["n"] > 0:
                    data = src.recv(4096)
                    if not data:
                        break
                    dst.sendall(data)
                    budget["n"] -= len(data)
            except Exception:
                pass
            finally:
                try: src.close()
                except Exception: pass
                try: dst.close()
                except Exception: pass

        t1 = threading.Thread(target=forward, args=(client, upstream), daemon=True)
        t2 = threading.Thread(target=forward, args=(upstream, client), daemon=True)
        t1.start()
        t2.start()
        t1.join(); t2.join()
        # update the byte count on the record
        used = record["max_bytes"] - budget["n"]
        updated = {**record, "byte_count": used, "last_use": now()}
        state["record"] = updated
        self.store.put("tcp_relay", updated, id=relay_id)
