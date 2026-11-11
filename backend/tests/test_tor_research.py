"""Tor connector regressions; a local fake SOCKS5 server verifies the route."""
import base64
import hashlib
import socket
import socketserver
import struct
import threading

import pytest

from bughunter.store import Store
from bughunter.tor_research import TorResearchService, validated_url


def onion_host():
    pubkey = bytes(range(32))
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + pubkey + version).digest()[:2]
    return base64.b32encode(pubkey + checksum + version).decode().lower().rstrip("=") + ".onion"


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        assert self._recv(3) == b"\x05\x01\x00"
        conn.sendall(b"\x05\x00")
        if self._recv(4) != b"\x05\x01\x00\x03": return
        name = self._recv(self._recv(1)[0]).decode()
        port = struct.unpack("!H", self._recv(2))[0]
        self.server.requests.append((name, port))
        conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk: break
            request += chunk
        target = request.split(b" ")[1].decode()
        body = {"/": b'<html><title>Research</title><body><h1>Topic</h1><a href="/thread">Thread</a><a href="http://other.com/">Other</a><script>BAD INSTRUCTION</script><p>Notes on protocols</p></body></html>',
                "/thread": b'<html><title>Thread</title><p>Follow-up notes about authentication</p></html>'}.get(target, b"Missing")
        status = b"200 OK" if target in {"/", "/thread"} else b"404 Not Found"
        conn.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    def _recv(self, n):
        value = b""
        while len(value) < n:
            part = self.request.recv(n - len(value))
            if not part: raise ConnectionError()
            value += part
        return value


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture
def socks():
    with Server(("127.0.0.1", 0), Handler) as srv:
        srv.requests = []
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield srv
        srv.shutdown()
        thread.join(timeout=2)


def test_onion_validation_and_ssrf():
    host = onion_host()
    assert validated_url("http://" + host) == "http://" + host + "/"
    for url in ("http://localhost/", "http://127.0.0.1/", "http://192.168.1.12/",
                "file:///etc/passwd", "http://foo.local/", "http://abc.onion/", "http://good.com:8080/",
                "https://name:pass@example.com", "http://example.com/#frag",
                "http://" + "a" * 56 + ".onion"):
        with pytest.raises(ValueError): validated_url(url)


def test_bookmark_read_and_review_workflow(tmp_path, socks):
    service = TorResearchService(Store(tmp_path))
    service.configure(socks.server_address[1])
    host = onion_host()
    item = service.add_source("http://" + host + "/", "Forum", "Learn protocol concepts", ["research"])
    assert service.add_source("http://" + host + "/")["duplicate"]
    assert service.status()["connected"]
    output = service.collect(item["id"], max_pages=3, max_depth=1)
    assert len(output["pages"]) == 2, output
    assert socks.requests == [(host, 80), (host, 80)]
    pages = service.list_pages(item["id"])
    assert any("Notes on protocols" in p["text"] for p in pages)
    assert not any("BAD INSTRUCTION" in p["text"] for p in pages)
    assert len(service.search("authentication", item["id"])) == 1
    with pytest.raises(ValueError, match="Review"):
        service.publish(pages[0]["id"])
    # Repeated captures update existing pages rather than duplicating them.
    reviewed = service.review_page(pages[0]["id"], True)
    service.collect(item["id"], max_pages=2, max_depth=1)
    assert len(service.list_pages(item["id"])) == 2
    assert service.store.get("tor_page", pages[0]["id"])["reviewed"]
    assert reviewed["reviewed"] is True
    assert service.list_sources()[0]["notes"] == "Learn protocol concepts"
    service.remove_source(item["id"])
    assert service.list_sources() == []
    assert len(service.list_pages()) == 2


def test_requires_tor_no_direct_fallback(tmp_path):
    service = TorResearchService(Store(tmp_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service.configure(port)
    assert not service.status()["connected"]
    source = service.add_source("http://example.com/")
    result = service.collect(source["id"])
    assert result["pages"] == [] and result["errors"]


def test_traversal_and_budgets(tmp_path):
    service = TorResearchService(Store(tmp_path))
    source = service.add_source("http://example.com/")
    for kwargs in ({"max_pages": 0}, {"max_pages": 13}, {"max_depth": 3}, {"max_depth": -1}):
        with pytest.raises(ValueError): service.collect(source["id"], **kwargs)


def test_publish_to_library_requires_review(tmp_path, socks):
    from bughunter.knowledge import KnowledgeService
    store = Store(tmp_path)
    knowledge = KnowledgeService(store, start_worker=False)
    service = TorResearchService(store, knowledge)
    service.configure(socks.server_address[1])
    source = service.add_source("http://" + onion_host() + "/")
    p = service.collect(source["id"])["pages"][0]
    service.review_page(p["id"], True)
    published = service.publish(p["id"])
    assert published["source_id"].startswith("source_")
    assert service.publish(p["id"])["duplicate"]


def test_specialist_training_promotion(tmp_path, socks):
    from bughunter.training import TrainingService
    store = Store(tmp_path)
    research = TorResearchService(store)
    research.configure(socks.server_address[1])
    source = research.add_source("http://" + onion_host() + "/")
    page = research.collect(source["id"])["pages"][0]
    training = TrainingService(store)
    other = training.create_workspace("burp_suite", "Different domain")
    tor = training.create_workspace("tor_research", "Tor analyst")
    with pytest.raises(ValueError): training.import_tor_page(tor["id"], page["id"], "Reviewed takeaway")
    research.review_page(page["id"], True)
    with pytest.raises(ValueError): training.import_tor_page(other["id"], page["id"], "Reviewed takeaway")
    example = training.import_tor_page(tor["id"], page["id"], "This source describes research notes.")
    assert example["domain"] == "tor_research" and example["status"] == "draft"
    training.review(example["id"], True)
    assert training.export(tor["id"])["manifest"]["reviewed_examples"] == 1


def test_agent_remote_provider_has_explicit_tor_optin(tmp_path):
    from types import SimpleNamespace
    from bughunter.model_tools import ModelTools
    store = Store(tmp_path)
    tor = TorResearchService(store)
    tor.add_source("http://" + onion_host() + "/", "Private research")
    run = store.put("run", {"project_id": "project_example", "status": "running"})
    source = store.put("source", {"tor_research_page_id": "example", "title": "Onion evidence"})
    public = store.put("source", {"title": "Public evidence"})
    knowledge = SimpleNamespace(search_for_project=lambda q, project_id, limit: [
        {"source_id": source["id"], "text": "restricted evidence"},
        {"source_id": public["id"], "text": "ordinary evidence"},
    ])
    provider = {"provider": "openai"}
    app = SimpleNamespace(store=store, tor=tor, knowledge=knowledge,
                          settings=SimpleNamespace(get=lambda: provider))
    agent = ModelTools(app, run["id"])
    with pytest.raises(ValueError, match="explicitly enable"):
        agent.call("tor_list_sources", {})
    assert [x["source_id"] for x in agent.call("search_knowledge", {"query": "evidence"})] == [public["id"]]
    with pytest.raises(ValueError, match="local inference"):
        agent.call("get_source", {"id": source["id"]})
    tor.configure(9050, allow_remote_ai=True)
    assert agent.call("tor_list_sources", {})[0]["title"] == "Private research"
    assert len(agent.call("search_knowledge", {"query": "evidence"})) == 2
    tor.configure(9050, allow_remote_ai=False)
    provider["provider"] = "ollama"
    assert len(agent.call("tor_list_sources", {})) == 1
