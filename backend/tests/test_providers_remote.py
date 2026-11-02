"""Remote Ollama provider: your own GPU server behind an authenticated HTTPS tunnel.

Covers the settings guardrails, the keyed probe, and one end-to-end agent loop
against a loopback fake that speaks the Ollama HTTP shape and REQUIRES the
Bearer key. remote_ollama is deliberately NOT in LOCAL_PROVIDERS: local-only
mode must reject it, and embeddings stay loopback-only (knowledge.py, unchanged).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import bughunter.providers as providers
from bughunter.app import Application
from bughunter.providers import LOCAL_PROVIDERS, PROVIDERS, ProviderSettings, probe_provider
from bughunter.store import Store


@pytest.fixture
def settings(tmp_path):
    return ProviderSettings(Store(tmp_path))


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get(name)

    def set_password(self, service, name, value):
        self.store[name] = value


@pytest.fixture
def keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    return fake


def test_remote_ollama_is_a_guarded_hosted_provider():
    assert "remote_ollama" in PROVIDERS
    assert "remote_ollama" not in LOCAL_PROVIDERS


def test_save_accepts_https_tunnel_url(settings):
    saved = settings.save({"provider": "remote_ollama", "model": "test:7b",
                           "api_base_url": "https://example.trycloudflare.com"})
    assert saved["provider"] == "remote_ollama"


def test_save_rejects_plain_http_public_endpoint(settings):
    with pytest.raises(ValueError, match="HTTPS"):
        settings.save({"provider": "remote_ollama", "model": "test:7b",
                       "api_base_url": "http://example.trycloudflare.com"})


def test_access_key_stores_for_remote_but_not_for_local(settings, keyring):
    settings.secret("remote_ollama", "tunnel-key-1")
    assert keyring.store["remote_ollama"] == "tunnel-key-1"
    with pytest.raises(ValueError, match="does not use an application-managed API key"):
        settings.secret("ollama", "nope")


def test_probe_requires_base_url_then_key(settings, keyring):
    probe = probe_provider({"provider": "remote_ollama", "model": "test:7b", "api_base_url": ""})
    assert probe["ok"] is False and "URL" in probe["detail"]
    probe = probe_provider({"provider": "remote_ollama", "model": "test:7b",
                            "api_base_url": "https://t.example.com"})
    assert probe["ok"] is False and "access key" in probe["detail"]


class _Handler(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        _Handler.seen["auth"] = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        return _Handler.seen.get("auth") == "Bearer tunnel-key-1"

    def do_GET(self):
        if self.path == "/api/tags" and self._authorized():
            self._send({"models": [{"name": "test:7b"}]})
        else:
            self._send({"error": "not found or unauthorized"}, 401)

    def do_POST(self):
        if self.path == "/api/show" and self._authorized():
            self._send({"capabilities": ["tools"]})
        elif self.path == "/api/chat" and self._authorized():
            self._send({"message": {"role": "assistant", "content": "Investigation complete; nothing queued."},
                        "done": True, "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        else:
            self._send({"error": "not found or unauthorized"}, 401)


@pytest.fixture
def server():
    _Handler.seen = {}
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_probe_lists_models_over_loopback(settings, keyring, server):
    settings.secret("remote_ollama", "tunnel-key-1")
    probe = probe_provider({"provider": "remote_ollama", "model": "test:7b", "api_base_url": server})
    assert probe["ok"] is True, probe


def test_probe_rejects_wrong_key(settings, keyring, server):
    settings.secret("remote_ollama", "wrong-key")
    probe = probe_provider({"provider": "remote_ollama", "model": "test:7b", "api_base_url": server})
    assert probe["ok"] is False


def test_agent_loop_sends_bearer_key_and_completes(keyring, server, tmp_path):
    app = Application(tmp_path, primary=False)
    try:
        app.policy.save_project({"id": "p", "name": "Owned test", "scope": ["https://example.test"],
                                 "authorization": "Fixture authorization"})
        app.dispatch("settings.save", {"provider": "remote_ollama", "model": "test:7b", "api_base_url": server,
                                       "input_price_per_million": 0, "output_price_per_million": 0,
                                       "pricing_confirmed": True})
        app.dispatch("settings.secret", {"provider": "remote_ollama", "value": "tunnel-key-1"})
        run = app.runs.start("p", "Summarize what you can see.")
        for _ in range(200):
            current = app.store.get("run", run["id"])
            if current["status"] in {"completed", "failed", "limited"}:
                break
            threading.Event().wait(0.05)
        current = app.store.get("run", run["id"])
        assert current["status"] == "completed", current.get("error")
        assert _Handler.seen.get("auth") == "Bearer tunnel-key-1"
    finally:
        app.shutdown()
