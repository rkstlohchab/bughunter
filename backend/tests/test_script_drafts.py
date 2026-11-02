"""Script drafts: text-only agent output for local, user-owned tools.

The workbench stores drafts and never executes them (AGENTS.md §2). Study mode
may draft from the studied source; investigation mode attaches drafts to the
project. Only the desktop lists, reviews, or deletes them.
"""
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


def source(app, source_id="src"):
    return app.store.put("source", {"id": source_id, "status": "ready", "chunk_count": 2, "title": "Cheat Engine course"})


def test_study_mode_can_draft_script_from_studied_source(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    draft = model.call("draft_script", {"title": "Freeze HP", "tool": "Cheat Engine Lua",
        "purpose": "Teach pointer scanning on my own offline save", "script": "local addr = getAddress('hp')",
        "notes": "Follows lesson 3", "source_ids": ["src"]})
    assert draft["status"] == "draft" and draft["reviewed"] is False
    assert draft["source_id"] == "src" and draft["project_id"] is None
    assert draft["run_id"].startswith("run_")


def test_investigation_mode_draft_attaches_to_project(app):
    app.policy.save_project({"id": "p", "name": "x", "scope": ["https://example.test"], "authorization": "ok"})
    r = app.store.put("run", {"kind": "investigate", "project_id": "p", "status": "running", "provider": "claude_code",
        "model": "opus", "prompt": "work", "settings_snapshot": {**DEFAULT_SETTINGS}, "events": []})
    model = ModelTools(app, r["id"], study=False)
    draft = model.call("draft_script", {"title": "Ghidra lesson", "tool": "Ghidra Python",
        "purpose": "Rename functions from a symbol table", "script": "for f in currentProgram.getFunctionManager(): ..."})
    assert draft["project_id"] == "p" and draft["source_id"] is None


def test_draft_script_requires_title_tool_script_purpose(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    base = {"title": "t", "tool": "Cheat Engine Lua", "purpose": "learn", "script": "x = 1"}
    with pytest.raises(ValueError, match="title"): model.call("draft_script", {**base, "title": " "})
    with pytest.raises(ValueError, match="local tool"): model.call("draft_script", {**base, "tool": ""})
    with pytest.raises(ValueError, match="script text"): model.call("draft_script", {**base, "script": "   "})
    with pytest.raises(ValueError, match="purpose"): model.call("draft_script", {**base, "purpose": "  "})


def test_draft_script_rejects_unknown_sources_and_huge_scripts(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    with pytest.raises(ValueError, match="cited source"):
        model.call("draft_script", {"title": "t", "tool": "Cheat Engine Lua", "purpose": "p", "script": "s", "source_ids": ["missing"]})
    with pytest.raises(ValueError, match="100,000"):
        model.call("draft_script", {"title": "t", "tool": "Cheat Engine Lua", "purpose": "p", "script": "x" * 100_001})


def test_draft_script_redacts_secrets_in_stored_text(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    draft = model.call("draft_script", {"title": "Config probe", "tool": "Cheat Engine Lua",
        "purpose": "Show why secrets must not live in scripts", 'script': 'local api_key = "sk-ant-abcdefghijklmnop"'})
    assert "sk-ant-abcdefghijklmnop" not in draft["script"] and "[REDACTED]" in draft["script"]


def test_desktop_lists_reviews_and_deletes_drafts(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    draft = model.call("draft_script", {"title": "Freeze HP", "tool": "Cheat Engine Lua",
        "purpose": "p", "script": "s", "source_ids": ["src"]})
    listed = app.dispatch("scripts.list")
    assert listed[0]["source_title"] == "Cheat Engine course"
    assert listed[0]["script"] == "s"
    reviewed = app.dispatch("scripts.review", {"id": draft["id"]})
    assert reviewed["reviewed"] is True and reviewed["reviewed_at"]
    deleted = app.dispatch("scripts.delete", {"id": draft["id"]})
    assert deleted["deleted"] is True and app.dispatch("scripts.list") == []
    with pytest.raises(ValueError, match="Draft not found"):
        app.dispatch("scripts.review", {"id": draft["id"]})
    with pytest.raises(ValueError, match="Unknown script-draft"):
        app.dispatch("scripts.draft", {"title": "t"})


def test_draft_script_still_blocked_for_target_tools_in_study_mode(app):
    source(app)
    model = ModelTools(app, study_run(app)["id"], study=True)
    with pytest.raises(ValueError, match="library tools"):
        model.call("draft_finding", {"title": "t", "summary": "s", "impact": "i", "steps": [], "evidence_ids": []})
    with pytest.raises(ValueError, match="library tools"):
        model.call("probe_url", {"url": "https://example.test"})


def test_deleting_project_removes_project_drafts_but_keeps_study_drafts(app):
    source(app)
    study_model = ModelTools(app, study_run(app)["id"], study=True)
    kept = study_model.call("draft_script", {"title": "Study draft", "tool": "Cheat Engine Lua", "purpose": "p", "script": "s"})
    app.policy.save_project({"id": "p", "name": "x", "scope": ["https://example.test"], "authorization": "ok"})
    r = app.store.put("run", {"kind": "investigate", "project_id": "p", "status": "running", "provider": "claude_code",
        "model": "opus", "prompt": "work", "settings_snapshot": {**DEFAULT_SETTINGS}, "events": []})
    project_draft = ModelTools(app, r["id"], study=False).call("draft_script",
        {"title": "Project draft", "tool": "Ghidra Python", "purpose": "p", "script": "s"})
    app.dispatch("projects.delete", {"id": "p"})
    remaining = {d["title"] for d in app.dispatch("scripts.list")}
    assert remaining == {"Study draft"} and kept["source_id"] == "src"
    assert app.store.get("script_draft", project_draft["id"]) is None
