"""DeepSeek provider: hosted OpenAI-compatible API with a keyring-stored key.

Covers the settings rules, the free /models key probe, and one end-to-end agent
loop against a loopback fake that speaks the OpenAI /chat/completions shape and
REQUIRES the Bearer key. deepseek is a hosted provider: local-only mode must
reject it, and deepseek-reasoner must be routed through the prompt-level tool
protocol (no native tools parameter).
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


def test_deepseek_is_a_guarded_hosted_provider():
    assert "deepseek" in PROVIDERS
    assert "deepseek" not in LOCAL_PROVIDERS


def test_save_accepts_default_base_url(settings):
    saved = settings.save({"provider": "deepseek", "model": "deepseek-chat"})
    assert saved["provider"] == "deepseek"


def test_access_key_stores_for_deepseek(settings, keyring):
    settings.secret("deepseek", "sk-test-1")
    assert keyring.store["deepseek"] == "sk-test-1"


def test_probe_requires_key_first(settings, keyring):
    probe = probe_provider({"provider": "deepseek", "model": "deepseek-chat",
                            "api_base_url": "", "pricing_confirmed": True})
    assert probe["ok"] is False and "API key" in probe["detail"]


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

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode() if length else ""
        if raw:
            _Handler.seen["last_body"] = raw

    def _authorized(self):
        _Handler.seen["auth"] = self.headers.get("Authorization")
        self._read_body()
        return _Handler.seen.get("auth") == "Bearer sk-test-1"

    def do_GET(self):
        if self.path == "/models" and self._authorized():
            self._send({"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})
        else:
            self._send({"error": "unauthorized"}, 401)

    def do_POST(self):
        if self.path == "/chat/completions" and self._authorized():
            self._send({"choices": [{"message": {"role": "assistant", "content": "Investigation complete; nothing queued."}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        else:
            self._send({"error": "unauthorized"}, 401)


@pytest.fixture
def server():
    _Handler.seen = {}
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_probe_lists_models_over_loopback(settings, keyring, server):
    settings.secret("deepseek", "sk-test-1")
    probe = probe_provider({"provider": "deepseek", "model": "deepseek-chat",
                            "api_base_url": server, "pricing_confirmed": True})
    assert probe["ok"] is True, probe
    assert "deepseek-reasoner" in probe["models_available"]


def test_probe_rejects_wrong_key(settings, keyring, server):
    settings.secret("deepseek", "sk-wrong")
    probe = probe_provider({"provider": "deepseek", "model": "deepseek-chat",
                            "api_base_url": server, "pricing_confirmed": True})
    assert probe["ok"] is False


def test_probe_flags_unconfirmed_pricing(settings, keyring, server):
    settings.secret("deepseek", "sk-test-1")
    probe = probe_provider({"provider": "deepseek", "model": "deepseek-chat",
                            "api_base_url": server, "pricing_confirmed": False})
    assert probe["ok"] is False and "pricing" in probe["detail"]


def test_probe_flags_unknown_model(settings, keyring, server):
    settings.secret("deepseek", "sk-test-1")
    probe = probe_provider({"provider": "deepseek", "model": "deepseek-v4-imaginary",
                            "api_base_url": server, "pricing_confirmed": True})
    assert probe["ok"] is False and "deepseek-chat" in probe["detail"]


def test_agent_loop_sends_bearer_key_and_completes(keyring, server, tmp_path):
    app = Application(tmp_path, primary=False)
    try:
        app.policy.save_project({"id": "p", "name": "Owned test", "scope": ["https://example.test"],
                                 "authorization": "Fixture authorization"})
        app.dispatch("settings.save", {"provider": "deepseek", "model": "deepseek-chat", "api_base_url": server,
                                       "input_price_per_million": 0, "output_price_per_million": 0,
                                       "pricing_confirmed": True})
        app.dispatch("settings.secret", {"provider": "deepseek", "value": "sk-test-1"})
        run = app.runs.start("p", "Summarize what you can see.")
        for _ in range(200):
            current = app.store.get("run", run["id"])
            if current["status"] in {"completed", "failed", "limited"}:
                break
            threading.Event().wait(0.05)
        current = app.store.get("run", run["id"])
        assert current["status"] == "completed", current.get("error")
        assert _Handler.seen.get("auth") == "Bearer sk-test-1"
        # deepseek-chat keeps native function calling: the tools parameter is sent.
        assert '"tools"' in (_Handler.seen.get("last_body") or "")
    finally:
        app.shutdown()


def test_reasoner_drops_native_tools_parameter(keyring, server, tmp_path):
    """deepseek-reasoner rejects the tools parameter, so the app must not send it."""
    app = Application(tmp_path, primary=False)
    try:
        app.policy.save_project({"id": "p", "name": "Owned test", "scope": ["https://example.test"],
                                 "authorization": "Fixture authorization"})
        app.dispatch("settings.save", {"provider": "deepseek", "model": "deepseek-reasoner", "api_base_url": server,
                                       "input_price_per_million": 0, "output_price_per_million": 0,
                                       "pricing_confirmed": True})
        app.dispatch("settings.secret", {"provider": "deepseek", "value": "sk-test-1"})
        run = app.runs.start("p", "Summarize what you can see.")
        for _ in range(200):
            current = app.store.get("run", run["id"])
            if current["status"] in {"completed", "failed", "limited"}:
                break
            threading.Event().wait(0.05)
        current = app.store.get("run", run["id"])
        assert current["status"] == "completed", current.get("error")
        assert '"tools"' not in (_Handler.seen.get("last_body") or "")
    finally:
        app.shutdown()
