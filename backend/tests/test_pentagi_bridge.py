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
