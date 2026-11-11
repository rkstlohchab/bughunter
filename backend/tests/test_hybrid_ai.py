"""Media never leaves through model tools; connection checks prove their stated level."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from bughunter.app import Application
from bughunter import providers
from bughunter.model_tools import ModelTools
from bughunter.local_media import LocalMediaRuntime, OLLAMA_HOST
from bughunter.store import Store


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def frame_tool(app, monkeypatch, **source_fields):
    app.settings.save({"provider":"openai_compatible", "model":"fixture", "api_base_url":"https://gpu.example/v1"})
    source = app.store.put("source", {"title":"Course", **source_fields})
    run = app.store.put("run", {"kind":"study", "source_id":source["id"], "project_id":None, "status":"running"})
    path = app.store.root / "frame.jpg"
    path.write_bytes(b"private image contents")
    monkeypatch.setattr(app.knowledge, "get_frame", lambda **_: {"source_id":source["id"], "artifact_id":"frame.jpg", "path":str(path), "time":12.5, "locator":"00:12.5"})
    return ModelTools(app, run["id"], study=True), {"source_id":source["id"], "artifact_id":"frame.jpg"}


def test_remote_reasoner_gets_only_local_vision_text(app, monkeypatch):
    model, arguments = frame_tool(app, monkeypatch)
    seen = []
    def vision(path, timestamp):
        seen.append((path.read_bytes(), timestamp))
        return "A settings menu is visible; the selected value is unclear."
    monkeypatch.setattr(app.media, "vision", vision)
    result = model.call("get_source_frame", arguments)
    assert seen == [(b"private image contents", 12.5)]
    assert result["processing"] == "local_vision" and result["status"] == "inferred"
    assert "unclear" in result["description"]
    assert "image_base64" not in result and "path" not in result
    assert "private image contents" not in json.dumps(result)


@pytest.mark.parametrize("fields", [{"project_id":"another-project"}, {"tor_research_page_id":"private-page"}])
def test_media_respects_project_and_remote_sharing(app, monkeypatch, fields):
    model, arguments = frame_tool(app, monkeypatch, **fields)
    monkeypatch.setattr(app.media, "vision", lambda *_: pytest.fail("private frame was analyzed"))
    for name in ("list_source_frames", "get_source_frame", "interpret_source_frame"):
        params = {"source_id":arguments["source_id"]} if name == "list_source_frames" else arguments
        if name == "interpret_source_frame":
            params = {**params, "text":"inferred"}
        with pytest.raises(ValueError, match="unavailable"):
            model.call(name, params)


@pytest.mark.parametrize("failure", ["offline", "empty"])
def test_local_vision_failure_does_not_forward_frame(app, monkeypatch, failure):
    model, arguments = frame_tool(app, monkeypatch)
    def vision(*_):
        if failure == "offline":
            raise ValueError("Local vision is unavailable")
        return ""
    monkeypatch.setattr(app.media, "vision", vision)
    with pytest.raises(ValueError):
        model.call("get_source_frame", arguments)


@pytest.mark.parametrize("info", [{"capabilities":["vision"], "remote_host":"cloud.example"}, {"capabilities":["completion"]}, {}])
def test_vision_checks_local_weights_before_uploading(tmp_path, monkeypatch, info):
    runtime = LocalMediaRuntime(Store(tmp_path))
    monkeypatch.setattr(runtime, "start", lambda: None)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=info)
    original = httpx.Client
    monkeypatch.setattr("bughunter.local_media.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"private-image")
    with pytest.raises(ValueError):
        runtime.vision(image, 0)
    assert [str(r.url) for r in calls] == [OLLAMA_HOST + "/api/show"]
    assert b"images" not in calls[0].content


def test_vision_disabled_does_not_start_model(tmp_path, monkeypatch):
    runtime = LocalMediaRuntime(Store(tmp_path))
    runtime.configure({"vision_enabled":False})
    monkeypatch.setattr(runtime, "start", lambda: pytest.fail("disabled vision started"))
    with pytest.raises(ValueError, match="disabled"):
        runtime.vision(tmp_path / "missing.jpg", 0)


def test_vision_sends_media_only_to_verified_loopback_model(tmp_path, monkeypatch):
    runtime = LocalMediaRuntime(Store(tmp_path))
    monkeypatch.setattr(runtime, "start", lambda: None)
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities":["completion", "vision"]})
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message":{"content":"A menu is visible."}})
    original = httpx.Client
    monkeypatch.setattr("bughunter.local_media.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"private-image")
    assert runtime.vision(image, 1) == "A menu is visible."
    assert [r.url.host for r in seen] == ["127.0.0.1", "127.0.0.1"]
    assert "images" not in json.loads(seen[0].content)
    assert json.loads(seen[1].content)["messages"][0]["images"]


def test_kaggle_probe_checks_models_without_inference(monkeypatch):
    monkeypatch.setattr(providers.keyring, "get_password", lambda *_: "secret")
    seen = []
    def handle(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data":[{"id":"served-model"}]})
    original = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = providers.probe_provider({"provider":"openai_compatible", "model":"served-model", "api_base_url":"https://gpu.example/v1"})
    assert result["ok"] and result["checked"] == "model_list" and result["tool_calling"] == "unverified"
    assert [(r.method, str(r.url)) for r in seen] == [("GET", "https://gpu.example/v1/models")]
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("url", ["http://gpu.example/v1", "https://user:secret@gpu.example/v1", "https://gpu.example/v1?key=secret", "file:///tmp/model", "https://gpu.example:bad/v1"])
def test_invalid_kaggle_url_never_reads_credentials(monkeypatch, url):
    monkeypatch.setattr(providers.keyring, "get_password", lambda *_: pytest.fail("credentials read for invalid URL"))
    assert not providers.probe_provider({"provider":"openai_compatible", "api_base_url":url})["ok"]


def test_kaggle_probe_does_not_follow_redirect_or_echo_secrets(monkeypatch):
    monkeypatch.setattr(providers.keyring, "get_password", lambda *_: "secret")
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"location":"https://other.example"}, text="secret")
    original = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = providers.probe_provider({"provider":"openai_compatible", "model":"model", "api_base_url":"https://gpu.example/v1"})
    assert not result["ok"] and len(seen) == 1 and "secret" not in json.dumps(result)


def load_smoke():
    path = Path(__file__).resolve().parents[2] / "scripts/check_kaggle_model.py"
    spec = importlib.util.spec_from_file_location("check_kaggle_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.smoke


@pytest.mark.parametrize("provider", ["openai_compatible", "remote_ollama"])
@pytest.mark.parametrize("behavior", ["pass", "wrong_tool", "no_tool", "ungrounded"])
def test_remote_tool_smoke_is_bounded_and_grounded(provider, behavior):
    seen = []
    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert request.headers["authorization"] == "Bearer secret"
        if len(seen) == 1:
            name = "write_memory" if behavior == "wrong_tool" else "read_check_value"
            arguments = {} if provider == "remote_ollama" else "{}"
            message = {"content":"", "tool_calls":[{"id":"call-1", "type":"function", "function":{"name":name, "arguments":arguments}}]}
            if behavior == "no_tool":
                message = {"content":"I already know the answer."}
        else:
            value = json.loads(payload["messages"][-1]["content"])["value"]
            message = {"content":"invented" if behavior == "ungrounded" else value}
        return httpx.Response(200, json={"message":message} if provider == "remote_ollama" else {"choices":[{"message":message}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = {"provider":provider, "model":"fixture", "api_base_url":"https://gpu.example/v1"}
        if behavior == "pass":
            assert load_smoke()(settings, "secret", client)["passed"]
        else:
            with pytest.raises(ValueError):
                load_smoke()(settings, "secret", client)
    assert len(seen) == (1 if behavior in {"wrong_tool", "no_tool"} else 2)
