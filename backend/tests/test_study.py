"""Course-study mode: a target-free run that only reads library sources and
records grounded graph assertions. It must never expose target/gateway tools."""
import pytest

from bughunter.app import Application
from bughunter.model_tools import ModelTools
from bughunter.providers import DEFAULT_SETTINGS


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def study_run(app, **overrides):
    return app.store.put("run", {"kind": "study", "source_id": "src", "project_id": None, "status": "running",
        "provider": "claude_code", "model": "opus", "prompt": "Study source src.",
        "settings_snapshot": {**DEFAULT_SETTINGS, "run_timeout_seconds": 10}, "events": [], **overrides})


def test_study_mode_blocks_target_tools(app):
    r = study_run(app)
    model = ModelTools(app, r["id"], study=True)
    cases = {
        "probe_url": {"url": "https://example.test"},
        "crawl_url": {"url": "https://example.test"},
        "passive_discovery": {"domain": "example.test"},
        "reviewed_checks": {"urls": ["https://example.test"]},
        "queue_action": {"request_json": "{}", "session_id": "s"},
        "execute_read_only": {"id": "x"},
        "draft_finding": {"title": "t", "summary": "s", "impact": "i", "steps": [], "evidence_ids": []},
    }
    for name, args in cases.items():
        with pytest.raises(ValueError, match="library tools"):
            model.call(name, args)


def test_study_mode_allows_library_tools(app):
    r = study_run(app)
    model = ModelTools(app, r["id"], study=True)
    # A library tool passes the study gate (empty KB simply returns no results).
    assert model.call("search_knowledge", {"query": "idor access control"}) == []


def test_investigation_mode_is_unaffected(app, monkeypatch):
    # Without study=True the same tool reaches its normal (non-"library tools") path.
    r = study_run(app, kind="investigate", project_id="p")
    app.policy.save_project({"id": "p", "name": "x", "scope": ["https://example.test"], "authorization": "ok"})
    model = ModelTools(app, r["id"], study=False)
    def unavailable(*args, **kwargs):
        raise ValueError('Discovery fixture unavailable')
    monkeypatch.setattr(app.tools, 'discover', unavailable)
    with pytest.raises(ValueError) as exc:
        model.call("passive_discovery", {"domain": "example.test"})
    assert "library tools" not in str(exc.value)


def test_start_study_validates_the_source(app, monkeypatch):
    monkeypatch.setattr(app.runs, "_preflight", lambda _: None)
    launched = []
    monkeypatch.setattr(app.runs, "_launch", lambda value: launched.append(value))
    with pytest.raises(ValueError, match="Source not found"):
        app.runs.start_study("missing")
    unprocessed = app.store.put("source", {"status": "queued", "chunk_count": 0, "title": "Course"})
    with pytest.raises(ValueError, match="Finish importing"):
        app.runs.start_study(unprocessed["id"])
    assert not launched
    ready = app.store.put("source", {"status": "ready", "chunk_count": 5, "title": "Bug bounty course"})
    run = app.runs.start_study(ready["id"])
    assert run["kind"] == "study" and run["source_id"] == ready["id"] and run["project_id"] is None
    assert len(launched) == 1


def test_get_source_pages_through_all_chunks(app, monkeypatch):
    many = {"id": "src", "title": "Course", "chunks": [{"id": f"c{i}", "text": f"text {i}", "locator": str(i)} for i in range(60)]}
    monkeypatch.setattr(app.knowledge, "get_source", lambda _id: dict(many))
    model = ModelTools(app, study_run(app)["id"], study=True)
    first = model.call("get_source", {"id": "src"})
    assert first["chunk_window"] == {"offset": 0, "count": 25, "total": 60, "next_offset": 25}
    assert [c["id"] for c in first["chunks"]] == [f"c{i}" for i in range(25)]
    middle = model.call("get_source", {"id": "src", "offset": 25})
    assert middle["chunk_window"]["next_offset"] == 50
    last = model.call("get_source", {"id": "src", "offset": 50})
    assert last["chunk_window"] == {"offset": 50, "count": 10, "total": 60, "next_offset": None}
    assert [c["id"] for c in last["chunks"]] == [f"c{i}" for i in range(50, 60)]


def test_start_study_sizes_turn_budget_to_course_length(app, monkeypatch):
    monkeypatch.setattr(app.runs, "_preflight", lambda _: None)
    monkeypatch.setattr(app.runs, "_launch", lambda value: None)
    one_page = app.store.put("source", {"status": "ready", "chunk_count": 10, "title": "S"})
    assert app.runs.start_study(one_page["id"])["settings_snapshot"]["max_agent_turns"] == 20  # default floor
    twelve = app.store.put("source", {"status": "ready", "chunk_count": 300, "title": "B"})
    assert app.runs.start_study(twelve["id"])["settings_snapshot"]["max_agent_turns"] == 30  # 12 pages * 2 + 6
    huge = app.store.put("source", {"status": "ready", "chunk_count": 5000, "title": "H"})
    assert app.runs.start_study(huge["id"])["settings_snapshot"]["max_agent_turns"] == 100  # capped at the ceiling


def test_start_study_respects_emergency_stop(app, monkeypatch):
    monkeypatch.setattr(app.runs, "_preflight", lambda _: None)
    monkeypatch.setattr(app.runs, "_launch", lambda value: None)
    ready = app.store.put("source", {"status": "ready", "chunk_count": 2, "title": "Course"})
    app.dispatch("system.stop")
    with pytest.raises(ValueError, match="stopped"):
        app.runs.start_study(ready["id"])
