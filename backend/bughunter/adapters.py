"""Typed integrations. Network tools require a verified final policy gateway."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .policy import PolicyService, canonical_url, validate_scope
from .store import Store, new_id, now
from .traffic import TrafficService, parse_raw, parse_response


def _installed(module):
    try: return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError): return False


def _loopback_proxy(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost", "::1"} or u.username or u.password or not u.port:
        raise ValueError("Configure an explicit loopback HTTP proxy URL")
    return url


class ToolService:
    def __init__(self, store: Store, policy: PolicyService, traffic: TrafficService):
        self.store, self.policy, self.traffic = store, policy, traffic
        self.gateway_process = None

    def status(self):
        gateway = self.store.setting("gateway", {})
        verified = bool(gateway.get("verified_at") and time.time()-gateway["verified_at"] < 300)
        from .tool_readiness import inventory
        burp_found = next((row["installed"] for row in inventory(self.store.setting("tool_locations", {})) if row["name"] == "Burp Suite"), False)
        tools = [
            {"id": "burp", "name": "Burp MCP", "installed": burp_found,
             "status": "connection_required" if burp_found else "not_installed",
             "capabilities": ["history", "repeater"],
             "detail": "Install Burp Suite and enable its MCP extension, then verify its connection." if not burp_found
                       else "Burp Suite is installed; its MCP extension is not connected or verified."},
            {"id": "gateway", "name": "Policy gateway", "installed": _installed("mitmproxy"), "status": "verified" if verified else "setup_required", "capabilities": ["scope", "approvals", "DNS pinning", "budgets"], "detail": "Built-in verified gateway works without Burp; Burp is an optional inspection route."},
            {"id": "playwright", "name": "Managed browser", "installed": _installed("playwright"), "status": "ready" if _installed("playwright") and verified else "setup_required", "capabilities": ["navigate", "click", "fill", "screenshots"]},
            {"id": "subfinder", "name": "Subfinder", "installed": bool(shutil.which("subfinder")), "status": "ready" if shutil.which("subfinder") else "not_installed", "capabilities": ["passive discovery"]},
            {"id": "nuclei", "name": "Nuclei", "installed": bool(shutil.which("nuclei")), "status": "ready" if shutil.which("nuclei") and verified else "setup_required", "capabilities": ["reviewed HTTP templates"]},
        ]
        return {"tools": tools, "gateway": {k:v for k,v in gateway.items() if k != "probe_nonce"},
                "unsupported": ["raw sockets", "arbitrary templates", "WebSocket execution", "unrestricted shell"]}

    def gateway_start(self, port: int = 8091):
        if not _installed("mitmproxy"): raise ValueError("Install backend tools dependencies to enable mitmproxy")
        port = int(port)
        if port < 1024 or port > 65535: raise ValueError("Gateway port must be between 1024 and 65535")
        if self.gateway_process and self.gateway_process.poll() is None:
            return {"status": "running", "port": self.store.setting("gateway_runtime", {}).get("port", port)}
        with socket.socket() as check:
            try: check.bind(("127.0.0.1", port))
            except OSError as exc: raise ValueError("Gateway port is already in use") from exc
        env = dict(os.environ)
        env["BUGHUNTER_DATA_DIR"] = str(self.store.root)
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--gateway", "--port", str(port)]
        else:
            args = [sys.executable, "-m", "bughunter.gateway", "--data-dir", str(self.store.root), "--port", str(port)]
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        logfile = self.store.root / "gateway.log"
        with logfile.open("ab") as log:
            self.gateway_process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
        for _ in range(100):
            if self.gateway_process.poll() is not None: raise ValueError("Gateway failed to start. Inspect gateway.log in the data directory.")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1):
                    if (self.store.root / "gateway/mitmproxy-ca-cert.pem").is_file(): break
                time.sleep(.1)
            except OSError: time.sleep(.1)
        else:
            self.close()
            raise ValueError("Gateway startup timed out")
        config = self.store.setting("gateway", {})
        config.update(port=port)
        config.pop("verified_at", None)
        self.store.set_setting("gateway", config)
        return {"status": "running", "port": port, "ca_path": str(self.store.root / "gateway/mitmproxy-ca-cert.pem"),
                "instructions": "Use built-in verification for autonomous read-only website review. Burp can optionally use this gateway as its upstream proxy."}

    def gateway_verify_direct(self):
        """Verify the built-in gateway directly; no external Burp installation.

        The CA is trusted only by the app's dedicated HTTP client. No OS-wide
        certificates, cookies, or proxy settings are modified.
        """
        import httpx
        import ssl
        runtime = self.store.setting('gateway_runtime', {})
        port = runtime.get('port')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Start the built-in gateway first.')
        ca = self.store.root / 'gateway' / 'mitmproxy-ca-cert.pem'
        if not ca.is_file():
            raise ValueError('Gateway CA not ready; restart the built-in gateway.')
        proxy_url = f'http://127.0.0.1:{port}'
        nonce = secrets.token_urlsafe(24)
        configuration = self.store.setting('gateway', {})
        configuration.update(burp_proxy=proxy_url, burp_ca=str(ca), probe_nonce=nonce, mode='built_in')
        configuration.pop('verified_at', None)
        self.store.set_setting('gateway', configuration)
        try:
            context = ssl.create_default_context(cafile=str(ca))
            with httpx.Client(proxy=proxy_url, verify=context, trust_env=False,
                              timeout=10, follow_redirects=False) as client:
                for scheme in ('http','https'):
                    response = client.get(f'{scheme}://bughunter-gateway.invalid/__probe',
                                          headers={'X-BugHunter-Probe':nonce})
                    receipt = self.store.setting('gateway_last_probe', {})
                    if (response.status_code != 200 or response.json().get('nonce') != nonce or
                        receipt.get('nonce') != nonce or receipt.get('scheme') != scheme):
                        raise ValueError('The request did not traverse the verified policy gateway.')
            configuration.update(verified_at=time.time(), verified_protocols=['http','https'])
            self.store.set_setting('gateway', configuration)
            return {'verified':True, 'mode':'built_in', 'expires_in_seconds':300, 'protocols':['http','https']}
        except Exception as exc:
            raise ValueError('The built-in gateway verification failed. Check gateway.log; do not run the audit until both probe protocols succeed.') from exc

    def gateway_verify(self, burp_proxy: str = "http://127.0.0.1:8080", burp_ca: str | None = None):
        import httpx
        _loopback_proxy(burp_proxy)
        if not burp_ca or not Path(burp_ca).expanduser().is_file(): raise ValueError("Select the exported Burp CA certificate in PEM format")
        ca = str(Path(burp_ca).expanduser().resolve())
        nonce = secrets.token_urlsafe(24)
        config = self.store.setting("gateway", {})
        config.update(burp_proxy=burp_proxy, burp_ca=ca, probe_nonce=nonce)
        config.pop("verified_at", None)
        self.store.set_setting("gateway", config)
        import ssl
        context = ssl.create_default_context(cafile=ca)
        try:
            with httpx.Client(proxy=burp_proxy, verify=context, trust_env=False, timeout=10, follow_redirects=False) as client:
                for scheme in ("http", "https"):
                    response = client.get(f"{scheme}://bughunter-gateway.invalid/__probe", headers={"X-BugHunter-Probe": nonce})
                    receipt = self.store.setting("gateway_last_probe", {})
                    if response.status_code != 200 or response.json().get("nonce") != nonce or receipt.get("nonce") != nonce or receipt.get("scheme") != scheme:
                        raise ValueError("The request did not traverse the application policy gateway")
            config["verified_at"] = time.time()
            config["verified_protocols"] = ["http", "https"]
            self.store.set_setting("gateway", config)
            return {"verified": True, "expires_in_seconds": 300, "protocols": ["http", "https"]}
        except Exception as exc:
            raise ValueError("Routing verification failed. Check Burp upstream proxy and the two dedicated CA trust settings.") from exc

    def _require_transport(self):
        config = self.store.setting("gateway", {})
        if not config.get("verified_at") or time.time()-config["verified_at"] > 300:
            raise ValueError("Connect and verify the built-in policy gateway or your Burp route before running network tools")
        _loopback_proxy(config["burp_proxy"])
        if self.store.setting("emergency_stop", False): raise ValueError("Emergency stop is active")
        return config

    def _adapter_ticket(self, project_id, session_id, run_id=None):
        ticket = secrets.token_urlsafe(32)
        with self.store.connection() as db:
            db.execute("INSERT INTO adapter_tickets(ticket,project_id,session_id,run_id,expires) VALUES(?,?,?,?,?)",
                       (ticket,project_id,session_id,run_id,time.time()+600))
        return ticket

    def _revoke_ticket(self, ticket):
        with self.store.connection() as db: db.execute("DELETE FROM adapter_tickets WHERE ticket=?", (ticket,))

    async def _burp_call(self, name, params):
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        endpoint = self.store.setting("settings",{}).get("burp_mcp_url", "http://127.0.0.1:9876/sse")
        u = urlsplit(endpoint)
        if u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost", "::1"}: raise ValueError("Burp MCP must be on loopback")
        async with sse_client(endpoint, timeout=10, sse_read_timeout=20) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                available = await session.list_tools()
                tools = {t.name for t in available.tools}
                if name not in tools: raise ValueError(f"Burp MCP does not expose {name}")
                # Caller cannot provide an arbitrary tool name; only these read/handoff operations exist here.
                if name not in {"get_proxy_http_history", "create_repeater_tab"}: raise ValueError("Unsupported Burp operation")
                result = await session.call_tool(name, params)
                if result.isError: raise ValueError("Burp returned an integration error")
                return {"content": [item.model_dump(mode="json") for item in result.content], "professional": "get_scanner_issues" in tools}

    def burp_history(self, project_id: str):
        project = self.policy.project(project_id)
        try: result = asyncio.run(self._burp_call("get_proxy_http_history", {"count": 50, "offset": 0}))
        except Exception as exc: raise ValueError(f"Cannot read Burp history: {type(exc).__name__}. Check the MCP extension and its history permission.") from exc
        imported, unparsed, excluded = [], 0, 0
        for block in result["content"]:
            if block.get("type") != "text": continue
            # Burp returns JSON encoded history objects, potentially separated by blank lines.
            text = block.get("text", "")
            decoder, offset, values = json.JSONDecoder(), 0, []
            while offset < len(text):
                while offset < len(text) and text[offset].isspace(): offset += 1
                if offset == len(text): break
                try:
                    value, end = decoder.raw_decode(text, offset); values.extend(value if isinstance(value,list) else [value]); offset = end
                except ValueError: unparsed += 1; break
            for value in values:
                try:
                    raw = value.get("request")
                    if isinstance(raw, dict): raw = raw.get("raw", raw.get("content", ""))
                    request = parse_raw(raw, value.get("url"))
                    validate_scope(project, request["url"], resolve=False)
                    response = value.get("response")
                    if isinstance(response, str): response = parse_response(response)
                    imported.append(self.traffic.record(project_id, request, response, origin="burp", trusted_observation=False))
                except ValueError: excluded += 1
                except (TypeError, AttributeError): unparsed += 1
        return {"imported": len(imported), "items": imported, "unparsed_blocks": unparsed, "excluded": excluded,
                "professional": result["professional"], "message": "For unparsed entries, use Burp XML export and import it in Traffic." if unparsed else "History imported."}

    def repeater(self, traffic_id: str):
        traffic = self.traffic.get(traffic_id)
        request = traffic["request"]
        validate_scope(self.policy.project(traffic["project_id"]), request["url"], resolve=False)
        url = urlsplit(request["url"])
        path = url.path or "/"
        if url.query: path += "?" + url.query
        headers = {**request.get("headers", {}), "Host": url.netloc}
        content = f"{request['method']} {path} HTTP/1.1\r\n" + "\r\n".join(f"{k}: {v}" for k,v in headers.items()) + "\r\n\r\n" + request.get("body", "")
        params = {"content": content, "targetHostname": url.hostname, "targetPort": url.port or (443 if url.scheme == "https" else 80),
                  "usesHttps": url.scheme == "https", "tabName": "BugHunter: review"}
        try: result = asyncio.run(self._burp_call("create_repeater_tab", params))
        except Exception as exc: raise ValueError("Could not create a Burp Repeater tab. Check MCP connectivity.") from exc
        return {"created": True, "sent": False, "result": result}

    def discover(self, project_id: str, domain: str, run_id: str | None = None):
        project = self.policy.project(project_id)
        domain = domain.strip().lower().rstrip(".")
        if not domain or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in domain): raise ValueError("Supply a DNS domain name")
        if domain not in {urlsplit(x).hostname for x in project["scope"]}: raise ValueError("Discovery domain must match a configured scope host")
        executable = shutil.which("subfinder")
        if not executable: raise ValueError("Install Subfinder to enable passive discovery")
        result = subprocess.run([executable, "-d", domain, "-silent", "-json", "-timeout", "10", "-max-time", "1"],
                                capture_output=True, text=True, timeout=75, shell=False)
        if result.returncode: raise ValueError("Subfinder failed; check its provider configuration")
        hosts = []
        for line in result.stdout.splitlines()[:5000]:
            try: host = json.loads(line).get("host")
            except ValueError: continue
            if host and (host == domain or host.endswith("."+domain)):
                hosts.append(host)
                self.store.put("asset", {"project_id": project_id, "host": host, "source": "subfinder", "status": "discovered", "in_scope": host == domain},
                               id="asset_"+hashlib.sha256(f"{project_id}:{host}".encode()).hexdigest()[:24])
        return {"hosts": sorted(set(hosts)), "message": "Discovered hosts do not expand authorized scope automatically."}

    def browser(self, project_id: str, url: str, steps: list | None = None, session_id: str = "browser", run_id: str | None = None):
        config = self._require_transport()
        project = self.policy.project(project_id)
        validate_scope(project, url)
        if not _installed("playwright"): raise ValueError("Install Playwright and its Chromium browser")
        if len(steps or []) > 20: raise ValueError("At most 20 explicit browser steps are supported per call")
        session_id = "browser:"+session_id.removeprefix("browser:")
        ticket = self._adapter_ticket(project_id, session_id, run_id)
        try: return asyncio.run(self._browser(project_id, url, steps or [], session_id, config, ticket))
        finally: self._revoke_ticket(ticket)

    async def _browser(self, project_id, url, steps, session_id, config, ticket):
        from playwright.async_api import async_playwright
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        ca = x509.load_pem_x509_certificate(Path(config["burp_ca"]).read_bytes())
        key = ca.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        spki = base64.b64encode(hashlib.sha256(key).digest()).decode()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy={"server": config["burp_proxy"]},
                args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--ignore-certificate-errors-spki-list="+spki])
            try:
                state_name = hashlib.sha256(f"{project_id}:{session_id}".encode()).hexdigest()
                state_path = self.store.root / "workspaces" / f"browser-{state_name}.json"
                context = await browser.new_context(service_workers="block", extra_http_headers={"X-BugHunter-Adapter": ticket}, accept_downloads=False,
                    storage_state=str(state_path) if state_path.exists() else None)
                await context.route_web_socket("**/*", lambda route: route.close())
                page = await context.new_page()
                # Prove this specific browser transport also reaches the gateway.
                await page.set_extra_http_headers({"X-BugHunter-Adapter":ticket,"X-BugHunter-Probe":config["probe_nonce"]})
                probe = await page.goto("https://bughunter-gateway.invalid/__probe",timeout=10000)
                if not probe or probe.status != 200 or (await probe.json()).get("nonce") != config["probe_nonce"]: raise ValueError("Browser routing probe failed")
                await page.set_extra_http_headers({"X-BugHunter-Adapter":ticket})
                await page.goto(url, wait_until="domcontentloaded", timeout=165000)
                for step in steps:
                    if self.store.setting("emergency_stop", False): raise ValueError("Emergency stop is active")
                    kind = step.get("action")
                    if kind == "click": await page.locator(step["selector"]).click(timeout=165000)
                    elif kind == "fill": await page.locator(step["selector"]).fill(str(step.get("value", "")), timeout=10000)
                    elif kind == "navigate": await page.goto(canonical_url(step["url"]), wait_until="domcontentloaded", timeout=165000)
                    else: raise ValueError("Supported browser steps are click, fill, and navigate")
                screenshot = self.store.root / "artifacts" / f"{new_id('browser')}.png"
                await page.screenshot(path=str(screenshot), full_page=False)
                await context.storage_state(path=str(state_path))
                state_path.chmod(0o600)
                if not self.store.get("project",project_id):
                    state_path.unlink(missing_ok=True)
                    screenshot.unlink(missing_ok=True)
                    raise ValueError("The project was deleted while the browser was open.")
                try:
                    self.store.put("asset",{"project_id":project_id,"kind":"screenshot","path":screenshot.name,"session_id":session_id})
                except ValueError:
                    state_path.unlink(missing_ok=True)
                    screenshot.unlink(missing_ok=True)
                    raise
                return {"url": page.url, "title": await page.title(), "screenshot": str(screenshot),
                        "pending_approvals": len(self.store.list("action", project_id=project_id, status="pending_approval")),
                        "message": "Browser requests that change state wait for desktop approval and Send once. Account storage is retained per project/session."}
            finally: await browser.close()

    def nuclei(self, project_id: str, urls: list[str], run_id: str | None = None,
               template_names: list[str] | None = None):
        config = self._require_transport()
        project = self.policy.project(project_id)
        if not urls or len(urls) > 50: raise ValueError("Provide between 1 and 50 scoped URLs")
        for url in urls: validate_scope(project, url)
        executable = shutil.which("nuclei")
        if not executable: raise ValueError("Install Nuclei to use reviewed HTTP checks")
        directory = self.store.root / "workspaces" / new_id("nuclei")
        directory.mkdir(mode=0o700)
        targets = directory / "targets.txt"
        targets.write_text("\n".join(urls))
        template = directory / "response-metadata.yaml"
        template.write_text('''id: bughunter-response-metadata
info:
  name: Response metadata observation
  author: bughunter
  severity: info
http:
  - method: GET
    path:
      - "{{BaseURL}}"
    redirects: false
    matchers:
      - type: status
        status:
          - 200
    extractors:
      - type: kval
        kval:
          - server
          - x_powered_by
''')
        # Operator-opt-in custom templates: resolve registered templates by name.
        from .capabilities import _toggles, list_custom_templates
        template_paths = [str(template)]
        if template_names:
            if not _toggles(self.store).get("nuclei_custom_templates"):
                raise ValueError("Enable the nuclei_custom_templates toggle first.")
            available = {entry["id"]: entry["path"] for entry in list_custom_templates(self.store)}
            for name in template_names:
                if name not in available:
                    raise ValueError(f"Unknown custom template: {name}")
                template_paths.append(available[name])
        ticket = self._adapter_ticket(project_id, "nuclei", run_id)
        args = [executable, "-l", str(targets)]
        for path in template_paths:
            args.extend(["-t", path])
        args.extend(["-proxy", config["burp_proxy"], "-proxy-internal", "-H", "X-BugHunter-Adapter: "+ticket,
                "-jsonl", "-silent", "-disable-update-check", "-no-interactsh", "-type", "http", "-rate-limit", "1", "-concurrency", "1", "-retries", "0", "-timeout", "10"])
        try:
            # The curated template has no code, raw, headless, DNS, or OOB requests.
            result = subprocess.run(args, capture_output=True, text=True, timeout=300, shell=False, cwd=directory)
            if result.returncode: raise ValueError("Nuclei failed; check version and proxy configuration")
            observations = []
            for line in result.stdout.splitlines()[:5000]:
                try: observations.append(json.loads(line))
                except ValueError: continue
            receipts = self.store.list("traffic", project_id=project_id, session_id="nuclei")
            return {"observations": observations, "gateway_receipts": len(receipts), "confirmed_findings": 0,
                    "message": "Metadata matches are observations. Review gateway evidence and application impact."}
        finally: self._revoke_ticket(ticket)

    def probe(self, project_id: str, url: str, session_id: str = "probe", run_id: str | None = None):
        action = self.policy.queue(project_id, {"method": "GET", "url": url, "headers": {}, "body": ""}, session_id,run_id=run_id)
        return self.policy.execute(action["id"]) if action["status"] == "ready" else action

    def crawl(self, project_id: str, url: str, limit: int = 20, run_id: str | None = None):
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        if not 1 <= int(limit) <= 100: raise ValueError("Crawl limit must be between 1 and 100")
        project = self.policy.project(project_id)
        queue, visited, actions = [url], set(), []
        while queue and len(visited) < limit:
            current = canonical_url(queue.pop(0))
            if current in visited: continue
            try: validate_scope(project, current)
            except ValueError: continue
            visited.add(current)
            action = self.probe(project_id, current, "crawler",run_id=run_id)
            actions.append(action["id"])
            response = action.get("response") or {}
            if action["status"] != "completed": continue
            if response.get("status") in {301,302,303,307,308}:
                location = next((v for k,v in response.get("headers", {}).items() if k.lower() == "location"), "")
                if location: queue.append(urljoin(current,location))
            if "<html" in response.get("body", "").lower():
                soup = BeautifulSoup(response["body"], "html.parser")
                queue.extend(urljoin(current, link["href"]) for link in soup.select("a[href]")[:100])
        return {"visited": sorted(visited), "action_ids": actions}

    def close(self):
        if self.gateway_process and self.gateway_process.poll() is None:
            self.gateway_process.terminate()
            try: self.gateway_process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.gateway_process.kill()

    def dispatch(self, method: str, params: dict):
        name = method.removeprefix("tools.")
        if name in {"status", "gateway_start", "gateway_verify", "gateway_verify_direct", "burp_history", "repeater", "browser", "discover", "nuclei", "probe", "crawl"}:
            return getattr(self, name)(**params)
        raise ValueError("Unknown tool operation")
