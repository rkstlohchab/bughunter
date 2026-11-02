"""Explicit user-selected documents are cited and bounded in ordinary chat."""
from unittest.mock import Mock
import pytest
from bughunter.chat import ChatService
from bughunter.store import Store
import bughunter.chat as chat

class Settings:
    def get(self):
        return {
            "provider":"openai_compatible", "model":"bughunter-moe",
            "api_base_url":"https://example.test/v1", "api_budget_usd":0.0,
            "input_price_per_million":0.0, "output_price_per_million":0.0,
            "pricing_confirmed":False, "local_only":False,
            "custom_system_prompt":"", "provider_system_prompts":{},
        }

class Library:
    def __init__(self, ready=True): self.ready = ready
    def search(self, query, limit): return []
    def get_source(self, sid):
        assert sid == "source_test"
        return {"id":sid,"title":"Guide.pdf","status":"ready" if self.ready else "queued",
                "chunks":[{"text":"Important verified finding.","locator":"page 4"}] if self.ready else []}


def test_chat_selected_documents_persist_and_cite(tmp_path, monkeypatch):
    calls=[]
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self, endpoint, headers, json):
            calls.append(json)
            response=Mock()
            response.json.return_value={"choices":[{"message":{"content":"I used page 4."}}]}
            return response
    monkeypatch.setattr(chat.httpx, "Client", Client)
    monkeypatch.setattr(chat.keyring, "get_password", lambda *args: "key")
    service=ChatService(Store(tmp_path / "db"), Settings(), Library())
    first=service.send({"message":"Explain this", "source_ids":["source_test"]})
    assert first["source_ids"] == ["source_test"]
    assert "Guide.pdf" in calls[0]["messages"][0]["content"]
    assert "page 4" in calls[0]["messages"][0]["content"]
    assert "Important verified finding" in calls[0]["messages"][0]["content"]
    assert "tools" not in calls[0]
    follow=service.send({"id":first["id"],"message":"More?"})
    assert follow["source_ids"] == ["source_test"]
    assert "Guide.pdf" in calls[1]["messages"][0]["content"]


def test_pending_document_blocks_send(tmp_path):
    service=ChatService(Store(tmp_path / "db"), Settings(), Library(ready=False))
    with pytest.raises(ValueError, match="still indexing"):
        service.send({"message":"Summarize", "source_ids":["source_test"]})
    assert not service.store.list("chat")


def test_invalid_document_selection_is_rejected(tmp_path):
    service=ChatService(Store(tmp_path / "db"), Settings(), Library())
    with pytest.raises(ValueError, match="six valid"):
        service.send({"message":"Hello", "source_ids":list(range(3))})
    with pytest.raises(ValueError, match="six valid"):
        service.send({"message":"Hello", "source_ids":["source_test"]*7})
