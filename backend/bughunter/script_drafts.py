"""Text-only script drafting for local, user-owned tools.

The agent can draft scripts (for example Cheat Engine Lua, Ghidra/IDA Python,
x64dbg script files) grounded in imported library knowledge. The workbench
never executes, ships, or transmits a draft: it stores plain text that a human
reviews and, if they choose, copies into their own tool on their own machine.
There is deliberately no execution adapter here (see AGENTS.md §2).
"""
from __future__ import annotations

from .security import redact
from .store import Store, now

MAX_SCRIPT_CHARS = 100_000


class ScriptDraftService:
    def __init__(self, store: Store):
        self.store = store

    def draft(self, data: dict, project_id: str | None = None, source_id: str | None = None,
              run_id: str | None = None) -> dict:
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("A draft needs a title.")
        tool = str(data.get("tool", "")).strip()
        if not tool:
            raise ValueError("Name the local tool this draft targets (for example 'Cheat Engine Lua').")
        script = str(data.get("script", ""))
        if not script.strip():
            raise ValueError("A draft needs script text for the human to review.")
        if len(script) > MAX_SCRIPT_CHARS:
            raise ValueError("Scripts are drafts for humans; keep them under 100,000 characters.")
        purpose = str(data.get("purpose", "")).strip()
        if not purpose:
            raise ValueError("Explain the draft's purpose so a human can review it safely.")
        source_ids = list(dict.fromkeys(data.get("source_ids", []) or []))
        for candidate in source_ids:
            if not self.store.get("source", candidate):
                raise ValueError("Every cited source must exist in the library.")
        if project_id and not self.store.get("project", project_id):
            raise ValueError("The draft's project no longer exists.")
        if source_id and not self.store.get("source", source_id):
            raise ValueError("The draft's studied source no longer exists.")
        value = {"title": title, "tool": tool, "purpose": purpose, "script": script,
                 "notes": str(data.get("notes", "")).strip(), "source_ids": source_ids,
                 "project_id": project_id, "source_id": source_id, "run_id": run_id,
                 "status": "draft", "reviewed": False}
        draft = self.store.put("script_draft", value)
        self.store.event("script.drafted", {"id": draft["id"], "tool": tool, "study": bool(source_id)})
        return redact(draft)

    def review(self, id: str, reviewed: bool = True) -> dict:
        draft = self.store.get("script_draft", id)
        if not draft:
            raise ValueError("Draft not found.")
        return self.store.put("script_draft", {**draft, "reviewed": bool(reviewed),
                                               "reviewed_at": now() if reviewed else None})

    def delete(self, id: str) -> dict:
        draft = self.store.get("script_draft", id)
        if not draft:
            raise ValueError("Draft not found.")
        self.store.delete("script_draft", id)
        return {"id": id, "deleted": True}

    def _with_source(self, draft: dict) -> dict:
        if draft.get("source_id"):
            source = self.store.get("source", draft["source_id"])
            if source:
                return {**draft, "source_title": source.get("title")}
        return draft

    def dispatch(self, method: str, params: dict):
        if method == "scripts.list":
            drafts = self.store.list("script_draft", **params)
            return [self._with_source(d) for d in drafts]
        if method == "scripts.get":
            draft = self.store.get("script_draft", params["id"])
            if not draft:
                raise ValueError("Draft not found.")
            return self._with_source(draft)
        if method == "scripts.review":
            return self.review(params["id"], bool(params.get("reviewed", True)))
        if method == "scripts.delete":
            return self.delete(params["id"])
        raise ValueError("Unknown script-draft operation")
