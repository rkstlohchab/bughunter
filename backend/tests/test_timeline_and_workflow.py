"""Timeline emit + Workflow engine basics (no external tools)."""
import pytest
from bughunter.app import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def test_timeline_emit_and_query(app):
    app.timeline.emit(kind="adapter", phase="start", tool="nmap",
                      run_id="run1", payload={"x": 1})
    app.timeline.emit(kind="adapter", phase="complete", tool="nmap",
                      run_id="run1", outcome="ok", duration_ms=120)
    events = app.timeline.query(run_id="run1")
    assert len(events) == 2
    kinds = {e["kind"] for e in events}
    assert kinds == {"adapter"}


def test_timeline_search(app):
    app.timeline.emit(kind="http", phase="complete", tool="curl",
                      payload={"url": "http://corporate.example"})
    hits = app.timeline.search(query="corporate")
    assert len(hits) == 1


def test_workflow_save_validate(app):
    rec = app.workflows.save(
        name="t",
        spec={"nodes": [{"id": "a", "adapter": "nmap", "args": {}}]},
    )
    assert rec["name"] == "t"
    # invalid: cyclic
    with pytest.raises(ValueError):
        app.workflows.save(
            name="bad",
            spec={"nodes": [
                {"id": "a", "adapter": "x", "depends_on": ["b"]},
                {"id": "b", "adapter": "x", "depends_on": ["a"]},
            ]},
        )


def test_workflow_templates_shipped(app):
    templates = app.workflows.templates()
    names = {t["name"] for t in templates}
    assert {"recon", "web-deep", "ad-lite"} <= names
