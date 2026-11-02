"""Read-only Tor research: explicit sources, SOCKS-hostname routing, reviewable captures.

The connector deliberately never uses a direct HTTP fallback. Remote HTML is
untrusted data: no scripts, forms, cookies, authentication, or model tool calls.
"""
from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import struct
from collections import deque
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from .store import now

MAX_BYTES = 1_000_000
MAX_TEXT = 120_000
MAX_PAGES = 12
MAX_DEPTH = 2
DEFAULT_PORT = 9050
MAX_BOOKMARKS = 100
USER_AGENT = "BugHunterResearch/0.1 (read-only; user-directed)"


def validated_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Enter a valid HTTP(S) research URL (2048 characters maximum).")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid URL or port.") from exc
    if (parts.scheme not in {"http", "https"} or not host or parts.username is not None or
            parts.password is not None or parts.fragment or any(ord(c) < 33 for c in value)):
        raise ValueError("Use a plain HTTP(S) URL without credentials or fragments.")
    if port not in (None, 80, 443):
        raise ValueError("Only HTTP and HTTPS default ports are supported.")
    if host.endswith(".onion"):
        label = host[:-6]
        if not re.fullmatch(r"[a-z2-7]{56}", label):
            raise ValueError("Use a complete 56-character v3 onion address.")
        try:
            decoded = base64.b32decode(label.upper())
            pubkey, checksum, version = decoded[:32], decoded[32:34], decoded[34:]
            wanted = hashlib.sha3_256(b".onion checksum" + pubkey + version).digest()[:2]
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("Malformed v3 onion address.") from exc
        if version != b"\x03" or checksum != wanted:
            raise ValueError("Invalid v3 onion address checksum.")
    else:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP addresses are not accepted as research sources.")
        if (host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")) or
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host)):
            raise ValueError("Enter a public hostname or a v3 onion service.")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _origin(url):
    p = urlsplit(url)
    return (p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80))


def _read_exact(stream, n):
    result = bytearray()
    while len(result) < n:
        chunk = stream.recv(n - len(result))
        if not chunk:
            raise ConnectionError("Tor SOCKS proxy closed the connection.")
        result += chunk
    return bytes(result)


def _socks_connect(host: str, target_port: int, proxy_port: int, timeout: float):
    if not isinstance(proxy_port, int) or not 1 <= proxy_port <= 65535:
        raise ValueError("Invalid local Tor SOCKS port.")
    # Fixed loopback proxy; never resolve a research hostname locally.
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        if _read_exact(sock, 2) != b"\x05\x00":
            raise ConnectionError("Tor SOCKS5 proxy refused unauthenticated access.")
        name = host.encode("ascii")
        if len(name) > 255:
            raise ValueError("Hostname is too long for SOCKS5.")
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack("!H", target_port))
        header = _read_exact(sock, 4)
        if header[:1] != b"\x05" or header[1] != 0 or header[2] != 0:
            raise ConnectionError(f"Tor could not open the destination (SOCKS code {header[1]}).")
        atyp = header[3]
        if atyp == 1:
            _read_exact(sock, 4)
        elif atyp == 4:
            _read_exact(sock, 16)
        elif atyp == 3:
            _read_exact(sock, _read_exact(sock, 1)[0])
        else:
            raise ConnectionError("Unexpected Tor SOCKS5 response.")
        _read_exact(sock, 2)
        return sock
    except BaseException:
        sock.close()
        raise


def _fetch_once(url: str, proxy_port: int, timeout=15):
    parsed = urlsplit(validated_url(url))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = _socks_connect(parsed.hostname, port, proxy_port, timeout)
    try:
        if parsed.scheme == "https":
            # Certificate validation stays enabled; onion HTTP also uses Tor's authenticated address.
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
        host_header = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
        packet = (f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nUser-Agent: {USER_AGENT}\r\n"
                  "Accept: text/html, text/plain\r\nAccept-Encoding: identity\r\n"
                  "Connection: close\r\n\r\n").encode("ascii")
        sock.sendall(packet)
        response = http.client.HTTPResponse(sock)
        response.begin()
        content_type = response.getheader("Content-Type", "").lower()
        encoding = response.getheader("Content-Encoding", "identity").lower().strip()
        if response.status in (301, 302, 303, 307, 308):
            return {"status": response.status, "location": response.getheader("Location", "")}
        if response.status != 200:
            raise ValueError(f"Research source returned HTTP {response.status}.")
        if not (content_type.startswith("text/html") or content_type.startswith("text/plain")):
            raise ValueError("Only HTML and plain text pages can be imported.")
        if encoding not in {"identity", ""}:
            raise ValueError("Compressed responses are not accepted by this read-only fetcher.")
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Page is larger than the 1 MB capture limit.")
        charset = "utf-8"
        match = re.search(r"charset=([a-z0-9_-]+)", content_type)
        if match:
            charset = match.group(1)
        try:
            content = body.decode(charset, "replace")
        except LookupError:
            content = body.decode("utf-8", "replace")
        return {"status": 200, "html": content, "type": content_type}
    finally:
        sock.close()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.ignore = 0
        self.title = ""
        self.in_title = False
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript", "svg"}:
            self.ignore += 1
        if tag == "title": self.in_title = True
        if tag in {"p", "h1", "h2", "h3", "li", "article", "section", "div", "br"}:
            self.text.append("\n")
        if tag == "a" and not self.ignore:
            href = dict(attrs).get("href", "")
            if href and len(href) <= 2048: self.links.append(href)
    def handle_endtag(self, tag):
        if tag in {"script", "style", "template", "noscript", "svg"} and self.ignore:
            self.ignore -= 1
        if tag == "title": self.in_title = False
    def handle_data(self, data):
        if self.ignore: return
        if self.in_title: self.title += data[:500]
        else: self.text.append(data)


def _page(url, proxy_port):
    first = validated_url(url)
    result = _fetch_once(first, proxy_port)
    # A redirect stays on the approved origin and may never downgrade HTTPS.
    for _ in range(3):
        if result["status"] == 200: break
        location = result.get("location", "")
        next_url = validated_url(urldefrag(urljoin(first, location)).url)
        if _origin(next_url) != _origin(first):
            raise ValueError("Cross-origin redirects require adding the destination as a separate source.")
        first = next_url
        result = _fetch_once(first, proxy_port)
    else:
        raise ValueError("Too many redirects.")
    if result["type"].startswith("text/plain"):
        title, content, links = first, result["html"], []
    else:
        parser = PageText()
        parser.feed(result["html"])
        title, content, links = (parser.title.strip() or first), " ".join("".join(parser.text).split()), parser.links
    return {"url": first, "title": title[:300], "text": content[:MAX_TEXT], "links": links}


class TorResearchService:
    def __init__(self, store, knowledge=None):
        self.store = store
        self.knowledge = knowledge
        from .tor_browser import TorBrowserController
        self.browser = TorBrowserController(self)
        from .research_browser_p3 import ResearchBrowserP3
        self.p3 = ResearchBrowserP3(self)

    def settings(self):
        return {"socks_port": self.store.setting("tor_socks_port", DEFAULT_PORT),
                "allow_remote_ai": self.store.setting("tor_allow_remote_ai", False)}

    def configure(self, socks_port, allow_remote_ai=None):
        if isinstance(socks_port, bool) or not isinstance(socks_port, int) or not 1 <= socks_port <= 65535:
            raise ValueError("Tor SOCKS port must be between 1 and 65535.")
        if allow_remote_ai is not None and not isinstance(allow_remote_ai, bool):
            raise ValueError("Remote AI sharing must be a boolean choice.")
        self.store.set_setting("tor_socks_port", socks_port)
        if allow_remote_ai is not None:
            self.store.set_setting("tor_allow_remote_ai", allow_remote_ai)
        return self.settings()

    def status(self):
        port = self.settings()["socks_port"]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
                sock.sendall(b"\x05\x01\x00")
                ok = _read_exact(sock, 2) == b"\x05\x00"
            return {"connected": ok, "socks_port": port, "detail": "SOCKS5 handshake available" if ok else "Unexpected SOCKS response"}
        except (ConnectionError, TimeoutError, OSError) as exc:
            return {"connected": False, "socks_port": port, "detail": "Local Tor SOCKS5 proxy not reachable"}

    def add_source(self, url, title="", notes="", tags=None, project_id=None):
        url = validated_url(url)
        if not isinstance(title, str) or len(title) > 160 or not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("Invalid source metadata.")
        if tags is None: tags = []
        if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or len(t) > 50 for t in tags):
            raise ValueError("Tags must be a list of short labels.")
        items = self.store.list("tor_source")
        for item in items:
            if item["url"] == url and item.get("project_id") == project_id:
                return {**item, "duplicate": True}
        if len(items) >= MAX_BOOKMARKS:
            raise ValueError("Research source limit reached.")
        return self.store.put("tor_source", {"url": url, "title": title.strip() or urlsplit(url).hostname,
            "notes": notes.strip(), "tags": tags, "origin": "operator_supplied", "project_id": project_id})

    def list_sources(self):
        return self.store.list("tor_source")

    def remove_source(self, id):
        if not self.store.get("tor_source", id): raise ValueError("Unknown research source.")
        return {"removed": self.store.delete("tor_source", id)}

    def list_pages(self, source_id=None):
        if source_id and not self.store.get("tor_source", source_id):
            raise ValueError("Unknown research source.")
        return [p for p in self.store.list("tor_page") if source_id is None or p["source_id"] == source_id]

    def collect(self, source_id, max_pages=1, max_depth=0, cancel_check=None):
        source = self.store.get("tor_source", source_id)
        if not source: raise ValueError("Unknown research source.")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
            raise ValueError(f"Capture between 1 and {MAX_PAGES} pages.")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= MAX_DEPTH:
            raise ValueError(f"Choose crawl depth 0–{MAX_DEPTH}.")
        base = validated_url(source["url"])
        queue = deque([(base, 0)])
        seen = set()
        results = []
        errors = []
        existing_by_url = {p["url"]: p for p in self.store.list("tor_page", source_id=source_id)}
        port = self.settings()["socks_port"]
        # Operator-opt-in direct-network fallback for public sources when Tor fails.
        from .capabilities import _toggles, direct_fetch
        fallback_allowed = _toggles(self.store).get("tor_direct_fallback", False)
        def _page_with_fallback(url_value: str, proxy_port: int) -> dict:
            try:
                return _page(url_value, proxy_port)
            except (ValueError, ConnectionError, TimeoutError, OSError, ssl.SSLError) as tor_exc:
                if not fallback_allowed:
                    raise
                if urlsplit(url_value).hostname and urlsplit(url_value).hostname.endswith(".onion"):
                    raise
                direct = direct_fetch(url_value, timeout=15)
                if direct.get("status") != 200:
                    raise ValueError(f"Direct fallback returned HTTP {direct['status']}") from tor_exc
                parser = PageText()
                parser.base = url_value
                parser.feed(direct["html"])
                return {"url": url_value, "title": (parser.title.strip() or url_value)[:300],
                         "text": (" ".join("".join(parser.text).split()))[:MAX_TEXT],
                         "links": parser.links}
        while queue and len(results) < max_pages:
            if cancel_check is not None and cancel_check():
                return {"pages":results,"errors":errors,"source_id":source_id,"requested":max_pages,"cancelled":True}
            url, depth = queue.popleft()
            if url in seen: continue
            seen.add(url)
            try:
                page = _page_with_fallback(url, port)
            except (ValueError, ConnectionError, TimeoutError, OSError, ssl.SSLError) as exc:
                errors.append({"url": url, "error": str(exc)[:300]})
                continue
            record = self.record_page(source_id, page, existing_by_url)
            results.append({"id": record["id"], "url": record["url"], "title": record["title"], "text_length": len(record["text"])})
            if depth >= max_depth: continue
            for link in page["links"][:250]:
                if link.startswith(("#", "mailto:", "javascript:", "data:")): continue
                try:
                    target = validated_url(urldefrag(urljoin(page["url"], link)).url)
                except ValueError:
                    continue
                if _origin(target) == _origin(base) and target not in seen and not any(target == a for a, _ in queue):
                    queue.append((target, depth + 1))
        return {"pages": results, "errors": errors, "source_id": source_id, "requested": max_pages}

    def record_page(self, source_id, page, existing_by_url=None):
        """Share the same review-reset and deduplication rules across both collectors."""
        existing_by_url = existing_by_url if existing_by_url is not None else {
            p["url"]: p for p in self.store.list("tor_page", source_id=source_id)}
        digest = hashlib.sha256((page["url"] + "\n" + page["text"]).encode()).hexdigest()
        previous = existing_by_url.get(page["url"])
        unchanged = previous is not None and previous.get("content_hash") == digest
        source = self.store.get("tor_source", source_id)
        record = self.store.put("tor_page", {"source_id": source_id, "project_id": source.get("project_id") if source else None, "url": page["url"],
            "title": page["title"], "text": page["text"], "content_hash": digest,
            "captured_at": now(), "reviewed": bool(previous.get("reviewed")) if unchanged else False,
            "library_source_id": previous.get("library_source_id") if unchanged else None},
            id=previous["id"] if previous else None)
        existing_by_url[page["url"]] = record
        return record

    def search(self, query, source_id=None, limit=30):
        if not isinstance(query, str) or len(query) > 300:
            raise ValueError("Search query must contain at most 300 characters.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Choose a result limit of 1–100.")
        words = re.findall(r"[\w-]+", query.lower())[:20]
        results = []
        for page in self.list_pages(source_id):
            hay = (page["title"] + " " + page["text"]).lower()
            score = sum(hay.count(word) for word in words)
            if words and not score: continue
            results.append({k: v for k, v in page.items() if k != "text"} |
                           {"excerpt": page["text"][:900], "score": score})
        return sorted(results, key=lambda r: (r["score"], r["captured_at"]), reverse=True)[:limit]

    def review_page(self, id, reviewed):
        item = self.store.get("tor_page", id)
        if not item: raise ValueError("Page not found.")
        if not isinstance(reviewed, bool): raise ValueError("Reviewed must be boolean.")
        return self.store.put("tor_page", {**item, "reviewed": reviewed}, id=id)

    def publish(self, id):
        item = self.store.get("tor_page", id)
        if not item or not item["reviewed"]: raise ValueError("Review this captured page before publishing it to the library.")
        if item.get("library_source_id"):
            return {"source_id": item["library_source_id"], "duplicate": True}
        if self.knowledge is None:
            raise ValueError("Knowledge Library is unavailable.")
        # Do not treat fetched pages as instructions to the agent.
        document = f"Research capture (untrusted source)\nURL: {item['url']}\nTitle: {item['title']}\nCaptured: {item['captured_at']}\n\n{item['text']}"
        imported = self.knowledge.import_source(filename=f"research_{id}.txt",
            content_base64=base64.b64encode(document.encode()).decode(), project_id=item.get("project_id"))
        self.knowledge._update(imported["id"], tor_research_page_id=id)
        self.store.put("tor_page", {**item, "library_source_id": imported["id"]}, id=id)
        return {"source_id": imported["id"], "status": imported["status"]}

    def dispatch(self, method, params):
        if method.startswith('tor.p3.'):
            return self.p3.dispatch(method, params)
        verbs = {
            "tor.settings": self.settings, "tor.configure": self.configure, "tor.status": self.status,
            "tor.sources": self.list_sources, "tor.add": self.add_source, "tor.remove": self.remove_source,
            "tor.pages": self.list_pages, "tor.collect": self.collect, "tor.search": self.search,
            "tor.review": self.review_page, "tor.publish": self.publish,
            "tor.browser.config": self.browser.config, "tor.browser.configure": self.browser.configure,
            "tor.browser.status": self.browser.status, "tor.browser.open": self.browser.open,
            "tor.browser.navigate": self.browser.navigate, "tor.browser.follow": self.browser.follow,
            "tor.browser.capture": self.browser.capture, "tor.browser.close": self.browser.close,
        }
        fn = verbs.get(method)
        if not fn: raise ValueError("Unknown Tor research operation.")
        # Record actual browser operations at their backend execution boundary.
        # This is not an AI-supplied claim that an action happened.
        tracking = method in {'tor.browser.open', 'tor.browser.follow', 'tor.browser.navigate',
                              'tor.browser.capture', 'tor.browser.close'}
        service = getattr(self, 'task_service', None)
        if tracking and service:
            if method == 'tor.browser.open':
                if self.browser.status().get('running'):
                    raise ValueError('A browser session is already running. Close it before opening another source.')
                session = service.browser_start(params.get('source_id'))
                self._browser_task_id = session['id']
            else:
                session = self.store.get('work_task', getattr(self, '_browser_task_id', ''))
                if not session or session.get('status') not in {'running', 'starting'}:
                    # An old browser session is never silently resumed after a restart.
                    raise ValueError('No active logged browser session. Open a saved source first.')
        try:
            result = fn(**params)
        except Exception as exc:
            if tracking and service and getattr(self, '_browser_task_id', None):
                service.browser_event(self._browser_task_id, method, error=exc)
                if method == 'tor.browser.open':
                    # Failed navigation can leave geckodriver running. Reap it.
                    try: self.browser.close()
                    except Exception: pass
                    self._browser_task_id = None
            raise
        if tracking and service:
            service.browser_event(self._browser_task_id, method, result=result)
            if method == 'tor.browser.close': self._browser_task_id = None
        if method.startswith('tor.browser.') and method not in {'tor.browser.status', 'tor.browser.config'}:
            self.store.event(method, {'source_id': params.get('source_id'),
                                       'capture_id': result.get('id') if method == 'tor.browser.capture' and isinstance(result, dict) else None})
        return result
