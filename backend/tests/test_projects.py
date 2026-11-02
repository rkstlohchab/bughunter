import base64

import pytest

from bughunter.app import Application
from bughunter.model_tools import ModelTools


def test_deleting_project_cancels_requests_removes_related_data_and_keeps_library(tmp_path):
    app = Application(tmp_path,primary=False)
    try:
        for id in ("delete","keep"):
            app.policy.save_project({"id":id,"name":id,"scope":["https://example.com"],"authorization":"Test"})
        source = app.knowledge.import_source(filename="lesson.md",content_base64=base64.b64encode(b"Keep the shared library.").decode())
        action = app.policy.queue("delete",{"method":"POST","url":"https://example.com/cart","body":"{}"})
        app.policy.approve(action["id"])
        run = app.store.put("run",{"project_id":"delete","status":"awaiting_approval"})
        for kind in ("traffic","test","finding","asset","observation"):
            app.store.put(kind,{"project_id":"delete"})
            app.store.put(kind,{"project_id":"keep"})
        app.store.event("run.event",{"run_id":run["id"],"text":"Delete this history"})
        app.store.set_setting("emergency_stop",False)
        app.tools._adapter_ticket("delete","test-account",run["id"])
        result = app.dispatch("projects.delete",{"id":"delete"})
        assert result["deleted"] and result["library_preserved"]
        assert app.store.get("project","delete") is None
        assert app.store.get("project","keep") is not None
        assert app.store.get("source",source["id"]) is not None
        assert app.store.get("action",action["id"]) is None
        assert app.store.get("run",run["id"]) is None
        assert not app.store.setting("emergency_stop")
        assert all(e["data"].get("run_id") != run["id"] for e in app.store.events())
        with pytest.raises(ValueError): app.policy.execute(action["id"])
        with pytest.raises(ValueError,match="deleted"): app.store.put("finding",{"project_id":"delete"})
        with app.store.connection() as db:
            assert db.execute("SELECT count(*) FROM adapter_tickets WHERE project_id='delete'").fetchone()[0] == 0
        for kind in ("traffic","test","finding","asset","observation"):
            assert len(app.store.list(kind,project_id="keep")) == 1
            assert app.store.list(kind,project_id="delete") == []
        with pytest.raises(ValueError,match="not available"):
            ModelTools(app,run["id"]).call("projects.delete",{"id":"keep"})
    finally: app.shutdown()
