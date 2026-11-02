"""Last-hop HTTP(S) policy gateway. Run separately with the optional mitmproxy extra.

Traffic path: managed tool -> Burp -> this gateway -> target. TLS interception
is limited to the dedicated Burp/client configuration, never the OS trust store.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from urllib.parse import urlsplit

from .policy import APP_HEADER, PolicyService, normalized_request, validate_scope
from .store import Store, now


class PolicyGateway:
    def __init__(self, store: Store):
        self.store = store
        self.policy = PolicyService(store)
        self.allowed_connections = {}
        self.pending_connections = {}

    def _response(self, flow, code, data):
        from mitmproxy.http import Response
        flow.response = Response.make(code, json.dumps(data).encode(), {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def requestheaders(self, flow):
        # Forwarding before full-body approval would invalidate the boundary.
        flow.request.stream = False

    def server_connect(self, data):
        try:
            self.store.require_desktop_owner()
        except ValueError as exc:
            data.server.error = str(exc)
            return
        # mitmproxy creates a fresh Server object after the request hook. Bind
        # authorization to the client and requested origin, then pin that new
        # connection here, before its socket is opened (not to the old object ID).
        key = (data.client.id, *data.server.address)
        candidates = self.pending_connections.get(key, {})
        authorized = next((item for id, item in candidates.items()
                           if (self.store.get("action", id) or {}).get("status") == "executing"), None)
        if not authorized or self.store.setting("emergency_stop", False):
            data.server.error = "BugHunter policy: connection has not passed request authorization"
            return
        data.server.address = authorized["pin"]
        data.server.sni = authorized["hostname"]
        self.allowed_connections[data.server.id] = authorized["pin"]

    async def request(self, flow):
        headers = {k.lower():v for k,v in flow.request.headers.items()}
        ticket = headers.pop(APP_HEADER, None)
        adapter_ticket = headers.pop("x-bughunter-adapter", None)
        probe = headers.pop("x-bughunter-probe", None)
        for name in list(flow.request.headers):
            if name.lower().startswith("x-bughunter-"): del flow.request.headers[name]
        if flow.request.pretty_host == "bughunter-gateway.invalid" and flow.request.path == "/__probe":
            config = self.store.setting("gateway", {})
            if not probe or probe != config.get("probe_nonce"):
                return self._response(flow, 403, {"error": "Invalid routing probe"})
            self.store.set_setting("gateway_last_probe", {"nonce": probe, "scheme": flow.request.scheme, "at": time.time()})
            return self._response(flow, 200, {"gateway": "BugHunterClaudex", "nonce": probe})
        if flow.request.headers.get("upgrade") or flow.request.method == "CONNECT":
            return self._response(flow, 403, {"error": "Protocol upgrades and raw tunnels are unavailable"})
        action = None
        reserved = False
        try:
            self.store.require_desktop_owner()
            if self.store.setting("emergency_stop", False): raise ValueError("Emergency stop is active")
            raw = flow.request.raw_content or b""
            if len(raw) > 2 * 1024 * 1024: raise ValueError("Request body exceeds 2 MiB")
            try: body = raw.decode("utf-8")
            except UnicodeDecodeError as exc: raise ValueError("Binary request replay is unavailable") from exc
            request = normalized_request({"method": flow.request.method, "url": flow.request.pretty_url,
                                          "headers": headers, "body": body})
            if ticket:
                action = self.policy.consume_ticket(ticket, request)
            elif adapter_ticket:
                with self.store.connection() as db:
                    context = db.execute("SELECT * FROM adapter_tickets WHERE ticket=? AND expires>?", (adapter_ticket,time.time())).fetchone()
                if not context: raise ValueError("Adapter session expired")
                action = self.policy.queue(context["project_id"], request, context["session_id"], context["run_id"])
                if action["status"] != "ready":
                    if context["session_id"].startswith("browser:"):
                        # Keep the real browser response path alive until the desktop
                        # approves AND releases this exact request. The model cannot release it.
                        until = time.time()+150
                        action = self.policy._change(action["id"],lambda a,db:a.update(gateway_held=True,gateway_hold_until=until))
                        while time.time() < until:
                            self.store.require_desktop_owner()
                            action = self.store.get("action",action["id"])
                            if self.store.setting("emergency_stop",False) or action["status"] in {"denied","cancelled","unknown"}:
                                raise ValueError("Held browser request was cancelled")
                            if not flow.client_conn.connected:
                                raise ValueError("Browser disconnected before approval")
                            if action["status"] == "approved" and action.get("release_requested"):
                                break
                            await asyncio.sleep(0.2)
                        else:
                            self.policy.cancel(action["id"])
                            raise ValueError("Browser approval window expired. No request was forwarded.")
                    else:
                        return self._response(flow, 428, {"approval_required": True, "action_id": action["id"],
                                                        "message": "Review this exact request in BugHunterClaudex before sending it."})
                action, single_ticket = self.policy._claim(action["id"])
                self.policy.consume_ticket(single_ticket, request)
            else:
                raise ValueError("A managed application request ticket is required")
            project = self.policy.project(action["project_id"])
            addresses = validate_scope(project, request["url"])
            delay = self.policy.reserve(action)
            reserved = True
            await asyncio.sleep(delay)
            self.store.require_desktop_owner()
            current = self.store.get("action", action["id"])
            if self.store.setting("emergency_stop", False) or current["status"] != "executing":
                raise ValueError("Action was cancelled before forwarding")
            # Approval must still bind the current scope after the rate-limit wait.
            self.policy._integrity(current, self.policy.project(action["project_id"]))
            if action.get("run_id"):
                run = self.store.get("run", action["run_id"])
                if not run or run["status"] not in {"running", "active", "awaiting_approval"}: raise ValueError("Run is no longer active")
            u = urlsplit(request["url"])
            port = u.port or (443 if u.scheme == "https" else 80)
            existing = flow.server_conn.peername
            if existing:
                if existing[0] not in addresses: raise ValueError("Existing connection no longer matches validated DNS")
                pinned = flow.server_conn.address
            else:
                pinned = (addresses[0], port)
            connection_key = (flow.client_conn.id, u.hostname, port)
            self.pending_connections.setdefault(connection_key, {})[action["id"]] = {"pin": pinned, "hostname": u.hostname}
            flow.metadata["bughunter_connection_key"] = connection_key
            flow.metadata["bughunter_action"] = action["id"]
            flow.metadata["bughunter_reserved"] = True
            self.store.event("gateway.forwarding", {"action_id": action["id"], "address": pinned[0]})
        except Exception as exc:
            if action:
                self.policy.finish(action["id"], error=str(exc), transmitted=False)
                if reserved: self.policy.release(action)
            self._response(flow, 403, {"error": str(exc), "action_id": action["id"] if action else None})

    def response(self, flow):
        id = flow.metadata.get("bughunter_action")
        if not id: return
        response = {"status": flow.response.status_code,
                    "headers": {k:v for k,v in flow.response.headers.items()},
                    "body": (flow.response.content or b"")[:2*1024*1024].decode("utf-8", "replace"),
                    "truncated": len(flow.response.content or b"") > 2*1024*1024}
        action = self.policy.finish(id, response=response)
        self.policy.release(action)
        flow.metadata.pop("bughunter_action", None)
        self._forget_request(flow, id)

    def error(self, flow):
        id = flow.metadata.get("bughunter_action")
        if id:
            action = self.policy.finish(id, error="Upstream connection failed; inspect state before retry", transmitted=True)
            self.policy.release(action)
            flow.metadata.pop("bughunter_action", None)
            self._forget_request(flow, id)

    def _forget_request(self, flow, id):
        key = flow.metadata.pop("bughunter_connection_key", None)
        pending = self.pending_connections.get(key)
        if pending is not None:
            pending.pop(id, None)
            if not pending: self.pending_connections.pop(key, None)

    def server_disconnected(self, data):
        self.allowed_connections.pop(data.server.id, None)


def create_addon(store: Store): return PolicyGateway(store)


async def run_gateway(store: Store, port: int = 8091):
    from mitmproxy import options
    from mitmproxy.tools.dump import DumpMaster
    confdir = store.root / "gateway"
    confdir.mkdir(mode=0o700, exist_ok=True)
    options_ = options.Options(listen_host="127.0.0.1", listen_port=port, confdir=str(confdir))
    master = DumpMaster(options_, with_termlog=False, with_dumper=False)
    master.options.update(connection_strategy="lazy", upstream_cert=False, body_size_limit="8m", http2=True, block_global=False)
    master.addons.add(PolicyGateway(store))
    store.set_setting("gateway_runtime", {"port": port, "started_at": now()})
    try: await master.run()
    finally: store.set_setting("gateway_runtime", {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    asyncio.run(run_gateway(Store(args.data_dir), args.port))


if __name__ == "__main__": main()
