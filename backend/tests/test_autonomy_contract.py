"""Autonomous mode skips approval clicks; it never relabels what a request does."""
import pytest

from bughunter.app import Application
from bughunter.policy import classify_effect, normalized_request


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    application.policy.save_project({"id": "p", "name": "Owned test", "scope": ["https://example.test"],
                                     "authorization": "Fixture authorization", "read_only_paths": ["/"]})
    yield application
    application.shutdown()


def req(method, path):
    return normalized_request({"method": method, "url": "https://example.test" + path})


def test_classification_is_truthful_even_with_autonomy_on(app):
    project = app.policy.project("p")
    assert app.dispatch("system.autonomy", {})["enabled"] is True
    assert classify_effect(project, req("GET", "/account/delete"))[0] == "state_changing"
    assert classify_effect(project, req("GET", "/logout"))[0] == "state_changing"
    assert classify_effect(project, req("GET", "/"))[0] == "read_only"


def test_autonomy_auto_releases_writes_and_off_restores_the_gate(app):
    on = app.policy.queue("p", {"method": "POST", "url": "https://example.test/cart", "body": "{}"})
    assert on["status"] == "approved" and on["auto_released"] is True and on["effect"] == "state_changing"
    assert on["approval"]["approved_by"] == "autonomous_mode" and on["approval"]["consumed"] is False
    assert app.dispatch("system.autonomy", {"enabled": False}) == {"enabled": False}
    assert app.dispatch("system.status", {})["autonomy_mode"] is False
    off = app.policy.queue("p", {"method": "POST", "url": "https://example.test/cart", "body": "{}"})
    assert off["status"] == "pending_approval" and off["auto_released"] is False


def test_flag_lookup_failure_fails_closed(app, monkeypatch):
    from bughunter import autonomy

    def broken(store):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(autonomy, "enabled", broken)
    action = app.policy.queue("p", {"method": "POST", "url": "https://example.test/cart", "body": "{}"})
    assert action["status"] == "pending_approval"


def test_models_cannot_toggle_autonomy(app):
    methods = {row["method"] for row in app.harness.actions.catalog(query="system", limit=50)["items"]}
    assert "system.status" in methods and "system.autonomy" not in methods
    with pytest.raises(ValueError, match="not exposed"):
        app.harness.actions.call("system.autonomy", {"enabled": True})


def test_rejects_non_boolean(app):
    with pytest.raises(ValueError):
        app.dispatch("system.autonomy", {"enabled": "yes"})
