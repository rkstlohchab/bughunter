"""Real loopback HTTP traffic through the packaged gateway implementation.

These prove gateway enforcement; they do not stand in for a live Burp test.
"""
import importlib.util
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from bughunter.adapters import ToolService
from bughunter.policy import APP_HEADER, PolicyService
from bughunter.store import Store
from bughunter.traffic import TrafficService

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mitmproxy") is None, reason="Install the tools extra for gateway integration tests")


@pytest.fixture
def gateway(tmp_path):
    hits = []
    class Target(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            hits.append((self.command, self.path, dict(self.headers)))
            if self.path == "/api/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/excluded")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/api/page":
                body = b'<html><title>Owned shop</title><form action="/api/cart" method="POST"><input name="quantity" value="1"><button>Update cart</button></form></html>'
            elif self.path == '/api/public-page':
                body = b'<html><title>Public fixture</title><body><div id="output"></div><script type="module" src="/api/public/a.js"></script><link rel="modulepreload" href="/api/public/b.js"><script>fetch("/api/cart",{method:"POST"});fetch("/excluded");</script></body></html>'
            elif self.path == '/api/public/a.js':
                body = b'import "./b.js"; document.getElementById("output").textContent += "Rendered asset. ";'
            elif self.path.startswith('/api/public/'):
                body = b'document.getElementById("output").textContent += "Rendered asset. ";'
            else:
                body = json.dumps({"method": self.command, "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/html" if self.path in {"/api/page", '/api/public-page'} else 'application/javascript' if self.path.endswith('.js') else "application/json")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.do_GET()
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    store = Store(tmp_path)
    policy = PolicyService(store)
    origin = f"http://127.0.0.1:{target.server_port}"
    policy.save_project({"id":"p", "name":"Owned loopback target", "scope":[origin+"/api"],
        "authorization":"Local integration fixture", "allow_private":True,
        "read_only_paths":["/api/products", "/api/redirect", "/api/page"], "requests_per_second":20})
    tools = ToolService(store, policy, TrafficService(store))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    try:
        tools.gateway_start(port)
        with httpx.Client(proxy=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
            yield store, policy, tools, client, origin, hits
    finally:
        tools.close()
        target.shutdown()
        target.server_close()


def send_claim(policy, client, origin, path="/api/products"):
    action = policy.queue("p", {"method":"GET", "url":origin+path, "headers":{}, "body":""})
    action, ticket = policy._claim(action["id"])
    result = client.get(origin+path, headers={APP_HEADER:ticket})
    return action, ticket, result


def test_gateway_requires_ticket_and_records_real_evidence(gateway):
    store, policy, tools, client, origin, hits = gateway
    assert client.get(origin+"/api/products").status_code == 403
    assert hits == []
    action, ticket, result = send_claim(policy, client, origin)
    assert result.status_code == 200, result.text
    assert len(hits) == 1
    assert not any(k.lower().startswith("x-bughunter-") for k in hits[0][2])
    final = store.get("action", action["id"])
    assert final["status"] == "completed"
    evidence = store.get("traffic", final["evidence_ids"][0])
    assert evidence["trusted_observation"] is True
    assert client.get(origin+"/api/products", headers={APP_HEADER:ticket}).status_code == 403
    assert len(hits) == 1


def test_out_of_scope_redirect_and_background_requests_never_reach_target(gateway):
    store, policy, tools, client, origin, hits = gateway
    ticket = tools._adapter_ticket("p", "managed-probe")
    response = client.get(origin+"/api/redirect", headers={"X-BugHunter-Adapter":ticket}, follow_redirects=True)
    assert response.status_code == 403
    assert [h[1] for h in hits] == ["/api/redirect"]
    assert client.get(origin+"/excluded", headers={"X-BugHunter-Adapter":ticket}).status_code == 403
    assert len(hits) == 1


def test_browser_write_is_held_until_approval_and_separate_release(gateway):
    store, policy, tools, client, origin, hits = gateway
    ticket = tools._adapter_ticket("p", "browser:account-a")
    with ThreadPoolExecutor() as pool:
        response = pool.submit(client.post, origin+"/api/cart", headers={"X-BugHunter-Adapter":ticket}, content='{"price":1}')
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            pending = store.list("action", status="pending_approval")
            if pending and pending[0].get("gateway_held"): break
            time.sleep(.02)
        assert pending and pending[0]["gateway_held"]
        action = pending[0]
        assert hits == []
        assert not response.done()
        policy.approve(action["id"])
        time.sleep(.25)
        assert hits == []
        policy.execute(action["id"])
        actual = response.result(timeout=5)
        assert actual.status_code == 200, actual.text
    assert len(hits) == 1
    assert hits[0][0] == "POST"
    assert store.get("action",action["id"])["approval"]["consumed"]
    with pytest.raises(ValueError): policy.execute(action["id"])
    assert len(hits) == 1


def test_emergency_stop_cancels_held_browser_write(gateway):
    store, policy, tools, client, origin, hits = gateway
    ticket = tools._adapter_ticket("p", "browser:account-b")
    with ThreadPoolExecutor() as pool:
        response = pool.submit(client.post, origin+"/api/cart", headers={"X-BugHunter-Adapter":ticket}, content='{"price":1}')
        deadline = time.monotonic()+5
        while not store.list("action") and time.monotonic() < deadline: time.sleep(.02)
        policy.stop()
        assert response.result(timeout=5).status_code == 403
    assert hits == []
    assert store.list("action")[0]["status"] == "cancelled"


def test_https_probe_terminates_locally_without_target_access(gateway):
    store, policy, tools, client, origin, hits = gateway
    gateway_port = store.setting("gateway")["port"]
    result = tools.gateway_verify(f"http://127.0.0.1:{gateway_port}", str(store.root/"gateway/mitmproxy-ca-cert.pem"))
    assert result["protocols"] == ["http", "https"]
    assert hits == []


def test_managed_chromium_uses_gateway_and_preserves_held_form_response(gateway):
    if importlib.util.find_spec("playwright") is None: pytest.skip("Playwright tools extra is required")
    store, policy, tools, client, origin, hits = gateway
    gateway_port = store.setting("gateway")["port"]
    tools.gateway_verify(f"http://127.0.0.1:{gateway_port}", str(store.root/"gateway/mitmproxy-ca-cert.pem"))
    with ThreadPoolExecutor() as pool:
        browser = pool.submit(tools.browser,"p",origin+"/api/page",[{"action":"click","selector":"button"}],"alice")
        deadline = time.monotonic()+20
        pending = []
        while time.monotonic() < deadline:
            if browser.done(): browser.result()  # Surface a transport failure immediately.
            pending = [a for a in store.list("action",status="pending_approval") if a.get("gateway_held") and a["request"]["method"] == "POST"]
            if pending: break
            time.sleep(.05)
        assert pending, "Browser form request must pause for review"
        assert all(h[0] == "GET" for h in hits)
        policy.approve(pending[0]["id"])
        policy.execute(pending[0]["id"])
        result = browser.result(timeout=10)
    assert result["url"] == origin+"/api/cart"
    assert sum(h[0] == "POST" for h in hits) == 1
    assert store.get("action",pending[0]["id"])["status"] == "completed"


def test_public_render_serializes_assets_and_blocks_writes_before_gateway(gateway):
    if importlib.util.find_spec('playwright') is None: pytest.skip('Playwright required')
    store, policy, tools, client, origin, hits = gateway
    project = policy.project('p')
    policy.save_project({**project, 'read_only_paths':['/api/public-page','/api/public/*'],
                         'max_concurrency':1, 'requests_per_second':2})
    tools.gateway_verify_direct()
    result = tools.browser('p', origin+'/api/public-page', session_id='public-fixture', public_only=True)
    assert result['text'].count('Rendered asset.') == 2
    assert len(result['evidence_ids']) == 3
    assert not result['failed_requests']
    assert result['coverage'] == 'partial'  # Excluded/write requests were blocked.
    assert {h[1] for h in hits} == {'/api/public-page','/api/public/a.js','/api/public/b.js'}
    assert all(h[0] == 'GET' for h in hits)
    assert any(r['method'] == 'POST' for r in result['blocked_requests'])
    assert not store.list('action', status='pending_approval')
    assert not list((store.root/'workspaces').glob('browser-*.json'))
