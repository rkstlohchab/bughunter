"""Disposable loopback shop with paired vulnerable/fixed pricing implementations."""
from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .policy import PolicyService, validate_scope
from .store import Store, now
from .traffic import TrafficService

CATALOG = {"regular": {"name": "Eligible test item", "price": 100.0, "coupon_eligible": True},
           "excluded": {"name": "Excluded test item", "price": 200.0, "coupon_eligible": False}}


def calculate_cart(body: dict, mode: str) -> dict:
    product_id = body.get("product_id", "excluded")
    if product_id not in CATALOG: raise ValueError("Unknown lab product")
    item = CATALOG[product_id]
    quantity = body.get("quantity", 1)
    if not isinstance(quantity, (int, float)) or isinstance(quantity, bool): raise ValueError("Quantity must be numeric")
    coupons = body.get("coupon", [])
    coupons = [coupons] if isinstance(coupons, str) and coupons else coupons or []
    if not isinstance(coupons, list) or any(c not in {"ELIGIBLE10", "MINIMUM20"} for c in coupons): raise ValueError("Unknown lab coupon")
    if mode != "vulnerable" and (quantity < 1 or int(quantity) != quantity or quantity > 20): raise ValueError("Quantity must be an integer from 1 to 20")
    canonical_subtotal = item["price"] * quantity
    valid_coupons = []
    if "ELIGIBLE10" in coupons and item["coupon_eligible"]: valid_coupons.append("ELIGIBLE10")
    if "MINIMUM20" in coupons and canonical_subtotal >= 250: valid_coupons.append("MINIMUM20")
    valid_coupons = valid_coupons[:1]
    canonical_total = round(canonical_subtotal * (0.9 if "ELIGIBLE10" in valid_coupons else 0.8 if "MINIMUM20" in valid_coupons else 1), 2)
    violations = []
    accepted_subtotal = canonical_subtotal
    accepted_coupons = valid_coupons
    if mode == "vulnerable":
        supplied_price = body.get("unit_price", item["price"])
        if not isinstance(supplied_price, (int, float)): raise ValueError("unit_price must be numeric")
        accepted_subtotal = supplied_price * quantity
        accepted_coupons = coupons
        if supplied_price != item["price"]: violations.append("Client-controlled price accepted")
        if "ELIGIBLE10" in coupons and not item["coupon_eligible"]: violations.append("Excluded item received coupon")
        if "MINIMUM20" in coupons and canonical_subtotal < 250: violations.append("Minimum-spend restriction bypassed")
        if len(coupons) > 1: violations.append("Prohibited coupon stacking accepted")
        if quantity < 1 or int(quantity) != quantity: violations.append("Invalid quantity accepted")
    accepted_total = accepted_subtotal
    for coupon in accepted_coupons: accepted_total *= 0.9 if coupon == "ELIGIBLE10" else 0.8
    accepted_total = round(accepted_total, 2)
    displayed_total = round(body.get("unit_price", item["price"]) * quantity * 0.9, 2) if mode == "display-only" else accepted_total
    return {"lab_evidence": True, "product_id": product_id, "quantity": quantity, "accepted_total": accepted_total,
            "canonical_total": canonical_total, "displayed_total": displayed_total, "accepted_coupons": accepted_coupons,
            "violations": violations, "explanation": "The authoritative lab cart contains the values shown here. Displayed values are tracked separately."}


class ShopServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), ShopHandler)
        self.nonce = secrets.token_urlsafe(32)
        self.carts = {}
        self.request_count = 0


class ShopHandler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def _reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/__identity": return self._reply(200, {"nonce": self.server.nonce})
        if path == "/.well-known/bughunter-price-audit.json":
            return self._reply(200, {"protocol":"bughunter-price-quote-v1", "environment":"staging",
                "quote_only":True, "orders_enabled":False, "payments_enabled":False,
                "challenge":self.server.nonce, "quote_path":"/__bughunter__/quote"})
        self.server.request_count += 1
        parts = path.strip("/").split("/")
        if len(parts) != 2 or parts[0] not in {"vulnerable", "fixed", "display-only"}: return self._reply(404, {"error": "Not found"})
        mode, route = parts
        account = self.headers.get("X-Test-Account", "alice")
        if route == "products": return self._reply(200, {"products": CATALOG})
        if route == "cart": return self._reply(200, self.server.carts.get((mode,account), {"empty": True}))
        return self._reply(404, {"error": "Not found"})

    def do_POST(self):
        self.server.request_count += 1
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts == ["__bughunter__", "quote"]:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 2048: raise ValueError("Body too large")
                body = json.loads(self.rfile.read(size))
                if (body.get("challenge") != self.server.nonce or body.get("test_sku") != "TEST-ITEM"
                        or body.get("dry_run") is not True or body.get("quantity") != 1):
                    return self._reply(400, {"error":"Invalid staging quote-only test contract"})
                candidate = body.get("client_unit_price_minor")
                if type(candidate) is not int: raise ValueError("Invalid proposed price")
                # The fixture is deliberately instrumented and never creates carts, orders or payments.
                price = candidate if getattr(self.server, "quote_vulnerable", False) else 10000
                return self._reply(200, {"quote_only":True,"order_created":False,"payment_initiated":False,
                                         "total_minor":price})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                return self._reply(400, {"error":str(exc)[:120]})
        if len(parts) != 2 or parts[0] not in {"vulnerable", "fixed", "display-only"} or parts[1] != "cart":
            return self._reply(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 100000: raise ValueError("Body too large")
            body = json.loads(self.rfile.read(size))
            result = calculate_cart(body, parts[0])
        except (ValueError, TypeError, KeyError) as exc: return self._reply(400, {"error": str(exc), "lab_evidence": True, "violations": []})
        account = self.headers.get("X-Test-Account", "alice")
        if account not in {"alice", "bob"}: return self._reply(401, {"error": "Use supplied test account alice or bob"})
        result["account"] = account
        self.server.carts[(parts[0],account)] = result
        return self._reply(200, result)


def execute_lab_action(policy: PolicyService, id: str):
    """A separate verified fixture transport, never a fallback for real targets."""
    initial = policy.store.get("action", id)
    runtime = policy.store.setting("lab_runtime", {})
    target = urlsplit(initial["request"]["url"])
    if not runtime or target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != runtime.get("port"):
        raise ValueError("This target is not the active disposable lab")
    check = http.client.HTTPConnection("127.0.0.1", runtime["port"], timeout=3)
    try:
        check.request("GET", "/__identity")
        reply = json.loads(check.getresponse().read())
    except Exception as exc: raise ValueError("The disposable lab is not running") from exc
    finally: check.close()
    if reply.get("nonce") != runtime.get("nonce"): raise ValueError("Lab identity mismatch")
    validate_scope(policy.project(initial["project_id"]), initial["request"]["url"])
    action, ticket = policy._claim(id)
    policy.consume_ticket(ticket, action["request"])
    delay = policy.reserve(action)
    conn = http.client.HTTPConnection("127.0.0.1", runtime["port"], timeout=10)
    try:
        time.sleep(delay)
        if policy.store.setting("emergency_stop", False) or policy.store.get("action", id)["status"] != "executing":
            raise ValueError("Action was cancelled before transmission")
        policy._integrity(action, policy.project(action["project_id"]))
        request = action["request"]
        path = target.path + ("?" + target.query if target.query else "")
        conn.request(request["method"], path, request["body"].encode(), request["headers"])
        response = conn.getresponse()
        result = {"status": response.status, "headers": dict(response.getheaders()), "body": response.read(2*1024*1024).decode("utf-8", "replace")}
        policy._change(id, lambda a, db: a.update(lab_verified=True))
        return policy.finish(id, response=result)
    except Exception as exc:
        return policy.finish(id, error=str(exc), transmitted=True)
    finally:
        conn.close()
        policy.release(action)


class LabService:
    def __init__(self, store: Store, policy: PolicyService, traffic: TrafficService):
        self.store, self.policy, self.traffic = store, policy, traffic
        self.server = None

    def start(self):
        if self.server: return {k:v for k,v in self.store.setting("lab_runtime").items() if k != "nonce"}
        self.server = ShopServer()
        threading.Thread(target=self.server.serve_forever, daemon=True, name="bughunter-lab").start()
        runtime = {"port": self.server.server_port, "url": f"http://127.0.0.1:{self.server.server_port}", "nonce": self.server.nonce, "started_at": now()}
        self.store.set_setting("lab_runtime", runtime)
        return {k:v for k,v in runtime.items() if k != "nonce"}

    def setup(self):
        runtime = self.start()
        base = runtime["url"]
        existing = self.store.list("project", lab_project=True)
        data = {"name": "Local commerce lab", "scope": [base], "exclusions": [], "authorization": "Disposable local test shop owned by this application. No real transactions.",
                "allow_private": True, "lab_project": True, "read_only_paths": [f"/{mode}/{route}" for mode in ("vulnerable", "fixed", "display-only") for route in ("products", "cart")],
                "requests_per_second": 10, "max_concurrency": 2, "max_requests": 1000}
        if existing: data["id"] = existing[0]["id"]
        project = self.policy.save_project(data)
        for previous in self.store.list("traffic", project_id=project["id"]):
            if previous.get("origin") == "lab_fixture": self.store.delete("traffic", previous["id"])
        items = []
        for mode in ("vulnerable", "fixed", "display-only"):
            request = {"method": "POST", "url": f"{base}/{mode}/cart", "headers": {"content-type": "application/json", "x-test-account": "alice"},
                       "body": json.dumps({"product_id": "excluded", "quantity": 1, "unit_price": 200, "coupon": ""})}
            items.append(self.traffic.record(project["id"], request, None, "alice", origin="lab_fixture", trusted_observation=False))
        return {"project": project, "traffic": items, "runtime": runtime, "message": "Choose lab traffic, propose tests, approve one request, execute it, then assess the result. Repeat against the fixed control."}

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.store.set_setting("lab_runtime", {})
        return {"stopped": True}

    def dispatch(self, method: str, params: dict):
        if method == "lab.setup": return self.setup()
        if method == "lab.start": return self.start()
        if method == "lab.stop": return self.stop()
        raise ValueError("Unknown lab operation")
