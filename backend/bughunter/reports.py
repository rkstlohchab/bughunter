from __future__ import annotations

import html
import json
import re

from .security import redact


class ReportService:
    def __init__(self, store):
        self.store = store

    def save(self, data: dict, agent: bool = False) -> dict:
        project = self.store.get("project", data.get("project_id", ""))
        if not project:
            raise ValueError("Choose a project before drafting a finding.")
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("A finding needs a title.")
        status = data.get("status", "hypothesis")
        if status not in {"hypothesis","observed","confirmed","false_positive","inconclusive"}:
            raise ValueError("Unknown finding status.")
        if agent and status == "confirmed":
            # Operator toggle allows agent confirmation when enabled; otherwise refuse.
            from .capabilities import _toggles
            if not _toggles(self.store).get("agent_can_confirm", False):
                raise ValueError("Only the desktop can confirm a finding after reviewing evidence.")
        evidence_ids = list(dict.fromkeys(data.get("evidence_ids", [])))
        trustworthy = False
        for evidence_id in evidence_ids:
            evidence = self.store.get("traffic",evidence_id) or self.store.get("observation",evidence_id) or self.store.get("action",evidence_id)
            if not evidence or evidence.get("project_id") != project["id"]:
                raise ValueError("Every evidence reference must belong to this project.")
            trustworthy = trustworthy or bool(evidence.get("trusted_observation") and evidence.get("response")) or bool(evidence.get("status") == "completed" and evidence.get("response") and evidence.get("evidence_ids"))
        if status == "confirmed":
            if not evidence_ids or not str(data.get("confirmation", "")).strip():
                raise ValueError("Confirmation requires evidence and an explanation of the server-accepted unauthorized outcome.")
            if not data.get("reviewed", False):
                raise ValueError("Review and attest to the evidence before confirming.")
            if not trustworthy:
                raise ValueError("Confirmation requires an actual completed execution or trusted gateway observation; imported text alone is insufficient.")
            if not str(data.get("controls", "")).strip():
                raise ValueError("Explain the baseline, fixed behavior, or account control that establishes a violation.")
        old = self.store.get("finding",data.get("id",""))
        if old and old["project_id"] != project["id"]:
            raise ValueError("A finding cannot move between projects.")
        value = {**(old or {}), **data, "title":title, "status":status, "evidence_ids":evidence_ids}
        return self.store.put("finding",value)

    def export(self, id: str, format: str = "markdown") -> dict:
        finding = self.store.get("finding",id)
        if not finding:
            raise ValueError("Finding not found.")
        evidence = []
        for eid in finding.get("evidence_ids",[]):
            item = self.store.get("traffic",eid) or self.store.get("observation",eid) or self.store.get("action",eid)
            if item:
                evidence.append(item)
        document = redact({"finding": finding, "evidence": evidence})
        f = document["finding"]
        steps = f.get("steps",[])
        if isinstance(steps,str):
            steps = steps.splitlines()
        md = f"# {f['title']}\n\nStatus: {f['status']}\n\n## Summary\n\n{f.get('summary','')}\n\n## Impact\n\n{f.get('impact','')}\n\n## Reproduction\n\n"
        md += "\n".join(f"{i+1}. {s}" for i,s in enumerate(steps))
        md += f"\n\n## Confirmation\n\n{f.get('confirmation','Not yet confirmed.')}\n\n## Controls\n\n{f.get('controls','Not recorded.')}\n\n## Evidence\n\n"
        md += "```json\n" + json.dumps(document["evidence"],indent=2,ensure_ascii=False) + "\n```\n"
        md += "\n## Supporting lessons\n\n" + ("```json\n" + json.dumps(f["citations"],indent=2,ensure_ascii=False)+"\n```\n" if f.get("citations") else "No cited lesson attached.\n")
        md += f"\nFinding reference: {f['id']}\n"
        if format == "json":
            content, ext = json.dumps(document,indent=2,ensure_ascii=False), "json"
        elif format == "markdown":
            content, ext = md, "md"
        elif format == "html":
            content, ext = '<!doctype html><html lang="en"><meta charset="utf-8"><title>'+html.escape(f["title"])+ '</title><style>body{max-width:900px;margin:48px auto;font:16px system-ui;line-height:1.6;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><body><pre>'+html.escape(md)+'</pre></body></html>', "html"
        else:
            raise ValueError("Choose markdown, html, or json.")
        filename = re.sub(r"[^a-zA-Z0-9_-]+","-", f["title"]).strip("-")[:70] + f"-{id[-8:]}.{ext}"
        path = self.store.root / "exports" / filename
        path.write_text(content,encoding="utf-8")
        path.chmod(0o600)
        return {"filename":filename,"content":content,"path":str(path)}
