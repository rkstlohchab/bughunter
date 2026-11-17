from types import SimpleNamespace

import pytest

from bughunter.haddix_methodology import checklist
from bughunter.pentagi_bridge import PentagiBridge, _endpoint


class Store:
    def get(self, kind, id):
        if kind == "project" and id == "p1":
            return {"id": "p1", "authorization": "recorded", "scope": ["https://shop.example.test/"],
                    "exclusions": ["https://shop.example.test/admin"]}
        return None


def test_endpoint_requires_tls_except_loopback():
    assert _endpoint("https://127.0.0.1:8443") == "https://127.0.0.1:8443"
    assert _endpoint("http://localhost:8443/") == "http://localhost:8443"
    for value in ("http://remote.example", "https://a.example/x", "https://x:y@a.example",
                  "https://a.example?token=unsafe", ""):
        with pytest.raises(ValueError):
            _endpoint(value)


def test_plan_stays_review_only_and_cites_haddix():
    result = PentagiBridge(Store()).plan("p1")
    assert result["launch_available"] is False
    assert "https://shop.example.test/" in result["prompt"]
    assert "https://shop.example.test/admin" in result["prompt"]
    assert "coupon" in result["prompt"].lower()
    assert "checkout" in result["prompt"].lower()
    assert len(checklist()) >= 6
    assert all(item["reference"].startswith("https://") for item in checklist())
    with pytest.raises(ValueError):
        PentagiBridge(Store()).plan("missing")


def test_flows_uses_bearer_and_sanitizes_response(monkeypatch):
    import bughunter.pentagi_bridge as bridge
    monkeypatch.setenv("PENTAGI_API_URL", "https://127.0.0.1:8443")
    monkeypatch.setenv("PENTAGI_API_TOKEN", "private-test-token")
    received = {}

    def fake_get(url, **kwargs):
        received.update(url=url, **kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"flows": [{"id": 4, "title": "Review only", "status": "finished",
                                     "secret": "should not appear"}]})
    monkeypatch.setattr(bridge.httpx, "get", fake_get)
    rows = PentagiBridge(Store()).flows()
    assert received["url"] == "https://127.0.0.1:8443/api/v1/flows"
    assert received["headers"]["Authorization"] == "Bearer private-test-token"
    assert received["verify"] is True
    assert rows == [{"id": "4", "title": "Review only", "status": "finished"}]


def test_bridge_exposes_no_mutation_commands():
    bridge = PentagiBridge(Store())
    with pytest.raises(ValueError):
        bridge.dispatch("pentagi.start", {"project_id": "p1"})

def test_selected_model_uses_subscription_without_new_key():
    from types import SimpleNamespace
    app = SimpleNamespace(store=Store(), settings=SimpleNamespace(
        get=lambda: {"provider": "codex", "model": "gpt-6", "local_only": False}),
        runs=SimpleNamespace())
    bridge = PentagiBridge(app)
    selection = bridge.dispatch("pentagi.selected_model", {})
    assert selection["provider"] == "codex"
    assert selection["kind"] == "subscription"
    assert selection["engine"] == "bughunter"
    assert "CLI subscriptions" in selection["external_pentagi_note"]


def test_native_run_reuses_selected_model_and_respects_explicit_launch(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv("PENTAGI_API_TOKEN", raising=False)
    requests = []
    def start(**kwargs):
        requests.append(kwargs)
        return {"id": "run_example", "status": "starting",
                "provider": "ollama", "model": "qwen-local"}
    app = SimpleNamespace(store=Store(), settings=SimpleNamespace(
        get=lambda: {"provider": "ollama", "model": "qwen-local", "local_only": True}),
        runs=SimpleNamespace(start=start))
    bridge = PentagiBridge(app)
    assert bridge.selected_model()["local_only"] is True
    with pytest.raises(ValueError, match="explicitly"):
        bridge.run_native("p1")
    assert requests == []
    result = bridge.dispatch("pentagi.run_native", {"project_id": "p1", "approved": True})
    assert result["engine"] == "bughunter"
    assert result["model"] == "qwen-local"
    assert len(requests) == 1
    assert requests[0]["project_id"] == "p1"
    assert "Haddix" in requests[0]["prompt"]
    with pytest.raises(ValueError, match="scope"):
        bridge.run_native("not-found", approved=True)
    assert len(requests) == 1
