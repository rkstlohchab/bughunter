"""Offline checks for opt-in Mac-local vision/audio integration."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bughunter.local_media import LocalMediaRuntime
from bughunter.store import Store
from bughunter.knowledge import KnowledgeService


def test_local_media_defaults_are_lightweight_and_local(tmp_path, monkeypatch):
    runtime = LocalMediaRuntime(Store(tmp_path))
    monkeypatch.setattr(runtime, "_ollama_tags", lambda: ["qwen3-vl:2b"])
    monkeypatch.setattr("bughunter.media_models.discover", lambda: [])
    status = runtime.status()
    assert status["config"]["vision_model"] == "qwen3-vl:2b"
    assert status["vision_ready"] is False  # Installed tags require an actual image test.
    assert runtime.whisper_path().parent == tmp_path / "models"
    assert runtime.config()["frame_interval"] >= 2
    assert runtime.config()["max_frames"] == 0


def test_install_requires_explicit_approval(tmp_path):
    runtime = LocalMediaRuntime(Store(tmp_path))
    for request in ({"target":"vision"}, {"target":"unknown", "approved": True}):
        with pytest.raises(ValueError):
            runtime.dispatch("media.setup", request)
    assert runtime.dispatch("media.configure", {"frame_interval": 3, "max_frames": 20})["max_frames"] == 20
    assert runtime.dispatch("media.configure", {"max_frames": 0})["max_frames"] == 0
    for request in ({"vision_model":"../model"}, {"frame_interval": 0}, {"max_frames": 1000001}):
        with pytest.raises(ValueError):
            runtime.dispatch("media.configure", request)


def test_control_and_timeline_are_per_source(tmp_path):
    store = Store(tmp_path)
    store.put("source", {"id":"source_video","status":"processing","title":"demo"})
    runtime = LocalMediaRuntime(store)
    assert runtime.dispatch("media.timeline", {"source_id":"source_video"})["items"] == []
    assert runtime.dispatch("media.control", {"source_id":"source_video","operation":"pause"})["operation"] == "pause"
    assert store.get("source", "source_video")["media_control"] == "pause"
    assert runtime.dispatch("media.control", {"source_id":"source_video","operation":"resume"})["operation"] == "resume"
    with pytest.raises(ValueError):
        runtime.dispatch("media.timeline", {"source_id":"missing"})
    with pytest.raises(ValueError):
        runtime.dispatch("media.control", {"source_id":"source_video","operation":"delete"})
    (tmp_path / "artifacts" / "source_video").mkdir(parents=True)
    (tmp_path / "artifacts" / "source_video" / "media_timeline.json").write_text(json.dumps([{"start":2.5, "text":"Clicked Settings", "modality":"vision"}]))
    assert runtime.dispatch("media.timeline", {"source_id":"source_video"})["items"][0]["start"] == 2.5


def test_media_extract_indexes_visual_timestamps(tmp_path, monkeypatch):
    store = Store(tmp_path)
    service = KnowledgeService(store, start_worker=False)
    runtime = LocalMediaRuntime(store)
    runtime.configure({"frame_interval":2,"max_frames":3})
    service.media_runtime = runtime
    source = store.put("source", {"id":"source_video","title":"Screen tutorial","status":"processing", "path":str(tmp_path/"demo.mp4")})
    (tmp_path / "demo.mp4").write_bytes(b"not a real video")
    def fake_run(args, timeout=1800):
        if "-show_streams" in args:
            return Mock(stdout=json.dumps({"format":{"duration":"6"},"streams":[{"codec_type":"video"}]}))
        Path(args[-1]).write_bytes(b"jpeg")
        return Mock(stdout="")
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(runtime, "vision", lambda path, t, **kwargs: f"At {t:.1f}s browser Settings menu is visible")
    monkeypatch.setattr(runtime, "checkpoint", lambda sid, service: None)
    result, warnings = service._extract_media(tmp_path/"demo.mp4", tmp_path/"artifacts"/"source_video", source)
    assert len([x for x in result if x["modality"] == "vision"]) == 3
    assert [x["start"] for x in result] == [0, 2, 4]
    assert any("sampled frames" in warning for warning in warnings)
    assert service._index(source, result) == 3
    graph = service.graph()
    assert any(edge["relation"] == "precedes" for edge in graph["edges"])
    service.shutdown()


def test_unlimited_frames_sample_full_duration_without_300_cap(tmp_path, monkeypatch):
    store = Store(tmp_path)
    service = KnowledgeService(store, start_worker=False)
    runtime = LocalMediaRuntime(store)
    runtime.configure({"frame_interval": 2, "max_frames": 0, "vision_enabled": False})
    service.media_runtime = runtime
    source = store.put('source', {'id': 'unlimited','title':'Long video','status':'processing'})
    path = tmp_path / 'demo.mp4'
    path.write_bytes(b'fake')
    samples = []
    def fake_run(args, timeout=1800):
        if '-show_streams' in args:
            return Mock(stdout=json.dumps({'format': {'duration': '602'}, 'streams':[{'codec_type':'video'}]}))
        samples.append(args[args.index('-ss')+1])
        Path(args[-1]).write_bytes(b'fake-jpg')
        return Mock(stdout='')
    monkeypatch.setattr(service, '_run', fake_run)
    monkeypatch.setattr(runtime, 'checkpoint', lambda *a: None)
    result, _ = service._extract_media(path, tmp_path/'artifacts'/'unlimited', source)
    assert len(samples) == 301
    assert samples[0] == '0.0'
    assert samples[-1] == '600.0'
    assert store.get('source','unlimited')['media_progress']['total'] == 301
    service.shutdown()
