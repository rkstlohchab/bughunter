"""API chat uses the configured model, persists messages and never invokes tools."""
from __future__ import annotations

from unittest.mock import Mock
import bughunter.chat as chat
from bughunter.store import Store


class Settings:
    def __init__(self, **overrides):
        self.value = {
            "provider": "openai_compatible", "model": "bughunter-moe",
            "api_base_url": "https://example.test/v1", "api_budget_usd": 0.0,
            "input_price_per_million": 0.0, "output_price_per_million": 0.0,
            "pricing_confirmed": True, "local_only": False,
            "custom_system_prompt": "", "provider_system_prompts": {},
        } | overrides
    def get(self):
        return self.value


def test_chat_free_self_hosted_persists_messages(tmp_path, monkeypatch):
    captured = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, endpoint, headers, json):
            captured.append((endpoint, headers, json))
            response = Mock()
            response.json.return_value = {"choices": [{"message": {"content": "AI reply"}}]}
            return response
    monkeypatch.setattr(chat.httpx, "Client", Client)
    monkeypatch.setattr(chat.keyring, "get_password", lambda service, provider: "private")
    svc = chat.ChatService(Store(tmp_path / "db"), Settings())
    first = svc.dispatch("chat.send", {"message": "Hello"})
    second = svc.dispatch("chat.send", {"id": first["id"], "message": "Follow up"})
    assert len(second["messages"]) == 4
    assert svc.dispatch("chat.get", {"id": first["id"]})["messages"][-1]["content"] == "AI reply"
    assert captured[0][0] == "https://example.test/v1/chat/completions"
    assert captured[0][2]["messages"][1]["content"] == "Hello"
    assert "tools" not in captured[0][2]
    assert svc.dispatch("chat.list", {})[0]["id"] == first["id"]


def test_chat_priced_zero_budget_blocks_request(tmp_path, monkeypatch):
    monkeypatch.setattr(chat.keyring, "get_password", lambda *args: "private")
    svc = chat.ChatService(Store(tmp_path / "db"), Settings(provider="openai", input_price_per_million=10.0))
    import pytest
    with pytest.raises(ValueError, match="API budget"):
        svc.send({"message": "Hello"})
    assert svc.store.list("chat") == []


def test_chat_invalid_input_does_not_call_model(tmp_path):
    import pytest
    svc = chat.ChatService(Store(tmp_path / "db"), Settings())
    with pytest.raises(ValueError, match="Enter a message"):
        svc.send({"message": " "})
    assert svc.store.list("chat") == []


def test_self_hosted_ignores_api_budget_when_pricing_not_confirmed(tmp_path, monkeypatch):
    payloads = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, endpoint, headers, json):
            payloads.append(json)
            result = Mock()
            result.json.return_value = {"choices": [{"message": {"content": "Works"}}]}
            return result
    monkeypatch.setattr(chat.httpx, "Client", Client)
    monkeypatch.setattr(chat.keyring, "get_password", lambda *args: "token")
    service = chat.ChatService(Store(tmp_path / "db"), Settings(
        api_budget_usd=0, input_price_per_million=10,
        pricing_confirmed=False, output_price_per_million=15))
    answer = service.send({"message": "Hi"})
    assert answer["messages"][-1]["content"] == "Works"
    assert payloads


def test_chat_uses_cited_knowledge_without_executing_tools(tmp_path, monkeypatch):
    payloads = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, endpoint, headers, json):
            payloads.append(json)
            result = Mock()
            result.json.return_value = {"choices": [{"message": {"content": "Cited explanation"}}]}
            return result
    class Knowledge:
        def search(self, query, limit):
            assert limit == 4
            return [{"text": "A documented behavior.", "title": "Local manual", "citation": {"locator": "p. 4"}}]
    monkeypatch.setattr(chat.httpx, "Client", Client)
    monkeypatch.setattr(chat.keyring, "get_password", lambda *args: "token")
    service = chat.ChatService(Store(tmp_path / "db"), Settings(pricing_confirmed=False), Knowledge())
    service.send({"message":"What does the manual say?"})
    assert "Local manual" in payloads[0]["messages"][0]["content"]
    assert "A documented behavior." in payloads[0]["messages"][0]["content"]
    assert "tools" not in payloads[0]
