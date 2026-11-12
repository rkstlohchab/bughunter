"""Separate, reviewable curricula for local retrieval and future fine-tuning.

Training samples never become instructions simply because they were imported.
Only explicitly reviewed instruction/response pairs enter exports. The three
specialties share a storage interface but not their datasets or prompt context.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from collections import Counter

from .security import redact_text
from .store import new_id, now

DOMAINS = {
    "burp_suite": "Burp Suite / authorized web testing",
    "cheat_engine": "Cheat Engine / local memory analysis",
    "tor_research": "Tor research / source-backed investigation",
}
MAX_EXAMPLES = 2000
MAX_EXPORT_BYTES = 12 * 1024 * 1024


def _text(value, field, limit=8000, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f"{field} must contain {'1–' if required else 'at most '}{limit} characters.")
    return value


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TrainingService:
    def __init__(self, store):
        self.store = store

    def _workspace(self, id):
        value = self.store.get("training_workspace", id)
        if value is None or value.get("domain") not in DOMAINS:
            raise ValueError("Training workspace not found.")
        return value

    def _example(self, id):
        value = self.store.get("training_example", id)
        if value is None or value.get("domain") not in DOMAINS:
            raise ValueError("Training example not found.")
        return value

    def create_workspace(self, domain, title, instructions="", model_source="", project_id=None):
        if domain not in DOMAINS:
            raise ValueError("Unknown training domain.")
        data = {"domain": domain, "title": _text(title, "Title", 120, True),
                "instructions": _text(instructions, "System prompt", 8000),
                "model_source": _text(model_source, "Model reference", 400),
                "purpose": DOMAINS[domain], "project_id": project_id}
        result = self.store.put("training_workspace", data)
        self.store.event("training.workspace_created", {"id": result["id"], "domain": domain})
        return result

    def update_workspace(self, id, title, instructions="", model_source=""):
        old = self._workspace(id)
        return self.store.put("training_workspace", {**old,
            "title": _text(title, "Title", 120, True),
            "instructions": _text(instructions, "System prompt", 8000),
            "model_source": _text(model_source, "Model reference", 400)}, id=id)

    def list_workspaces(self, domain=None):
        if domain is not None and domain not in DOMAINS:
            raise ValueError("Unknown training domain.")
        records = self.store.list("training_workspace")
        records = [r for r in records if r.get("domain") in DOMAINS and (domain is None or r["domain"] == domain)]
        counts = Counter(e["workspace_id"] for e in self.store.list("training_example"))
        reviewed = Counter(e["workspace_id"] for e in self.store.list("training_example") if e["status"] == "reviewed")
        return [{**r, "example_count": counts[r["id"]], "reviewed_count": reviewed[r["id"]]} for r in records]

    def save_example(self, workspace_id, instruction, response, context="", source="", notes="", group="", tags=None, id=None):
        workspace = self._workspace(workspace_id)
        existing = self._example(id) if id else None
        if existing and existing["workspace_id"] != workspace_id:
            raise ValueError("Examples cannot be transferred between workspaces.")
        if len(self.store.list("training_example", workspace_id=workspace_id)) >= MAX_EXAMPLES and not existing:
            raise ValueError("Workspace example limit reached.")
        instruction = _text(instruction, "Instruction", 16000, True)
        response = _text(response, "Desired response", 24000, True)
        context = _text(context, "Context", 12000)
        source = _text(source, "Source reference", 1000)
        notes = _text(notes, "Notes", 3000)
        group = _text(group, "Scenario group", 160)
        if tags is None: tags = []
        if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(x, str) or len(x) > 60 for x in tags):
            raise ValueError("Tags must be a list of up to 12 short strings.")
        # Do not save newly submitted examples as 'reviewed', even if editing a reviewed row.
        # A human reviews the final text again after a modification.
        fingerprint = _digest("\0".join([instruction.casefold(), context.casefold(), response.casefold()]))
        for entry in self.store.list("training_example", workspace_id=workspace_id):
            if entry["id"] != id and entry["fingerprint"] == fingerprint:
                raise ValueError("This instruction/response pair already exists in this workspace.")
        result = self.store.put("training_example", {
            **(existing or {}), "workspace_id": workspace_id, "domain": workspace["domain"], "project_id": workspace.get("project_id"),
            "instruction": instruction, "response": response, "context": context,
            "source": source, "notes": notes, "group": group,
            "tags": list(dict.fromkeys(x.strip() for x in tags if x.strip())),
            "fingerprint": fingerprint, "status": "draft", "reviewed_at": None, "training_allowed": False,
        }, id=id)
        self.store.event("training.example_saved", {"id": result["id"], "workspace_id": workspace_id})
        return result

    def list_examples(self, workspace_id, query="", status=None):
        self._workspace(workspace_id)
        if status not in (None, "draft", "reviewed"):
            raise ValueError("Unknown review status.")
        query = _text(query, "Query", 300).casefold()
        items = self.store.list("training_example", workspace_id=workspace_id)
        return [e for e in items if (status is None or e["status"] == status) and (
            not query or query in " ".join(str(e.get(k, "")) for k in ("instruction", "response", "notes", "tags", "source")).casefold())]

    def review(self, id, approved):
        if not isinstance(approved, bool):
            raise ValueError("approved must be boolean.")
        old = self._example(id)
        return self.store.put("training_example", {**old, "status": "reviewed" if approved else "draft",
                          "reviewed_at": now() if approved else None}, id=id)

    def delete_example(self, id):
        self._example(id)
        self.store.delete("training_example", id)
        return {"deleted": True}

    def import_tor_page(self, workspace_id, page_id, summary):
        """Promote a reviewed research capture to a draft supervised example.

        The operator writes the desired response; raw fetched text is context,
        never a machine-authored answer or an instruction source.
        """
        ws = self._workspace(workspace_id)
        if ws["domain"] != "tor_research":
            raise ValueError("Tor captures belong only in a Tor research curriculum.")
        page = self.store.get("tor_page", page_id)
        if not page or not page.get("reviewed"):
            raise ValueError("Review the captured page first.")
        summary = _text(summary, "Reviewed takeaway", 8000, True)
        return self.save_example(
            workspace_id, "Explain the verified takeaway of the supplied research source with source attribution.",
            summary, context="Untrusted research source:\n" + page["url"] + "\n" + page["text"][:10000],
            source="tor_page:" + page_id, group=page["source_id"], tags=["tor-research", "source-backed"])

    def delete_workspace(self, id):
        self._workspace(id)
        with self.store.connection(immediate=True) as db:
            db.execute("DELETE FROM entities WHERE kind='training_example' AND json_extract(data,'$.workspace_id')=?", (id,))
            db.execute("DELETE FROM entities WHERE kind='training_workspace' AND id=?", (id,))
        return {"deleted": True}

    def search(self, domain, query, limit=5):
        """Use only previously reviewed examples; never mix specialist memories."""
        if domain not in DOMAINS:
            raise ValueError("Unknown training domain.")
        query = _text(query, "Query", 300, True)
        terms = set(re.findall(r"[\w-]{3,}", query.casefold()))
        matches = []
        for entry in self.store.list("training_example"):
            if entry["domain"] != domain or entry["status"] != "reviewed":
                continue
            haystack = " ".join(str(entry.get(k, "")) for k in ("instruction", "response", "tags", "notes")).casefold()
            score = sum(1 for term in terms if term in haystack)
            if score:
                matches.append((score, entry))
        return [{k: e[k] for k in ("id", "workspace_id", "instruction", "response", "source", "notes", "tags")}
                for _, e in sorted(matches, key=lambda pair: (-pair[0], pair[1]["id"]))[:max(1, min(int(limit), 10))]]

    def export(self, workspace_id):
        """Generate isolated MLX-LM chat JSONL, splits and a provenance manifest.

        All variants in the same scenario group go in the same split. This avoids
        evaluating on nearly identical variants from training data.
        """
        ws = self._workspace(workspace_id)
        reviewed = [e for e in self.store.list("training_example", workspace_id=workspace_id) if e["status"] == "reviewed"]
        if not reviewed:
            raise ValueError("Review at least one example before exporting training data.")
        families = {}
        for e in reviewed:
            family = e["group"] or e["id"]
            families.setdefault(family, []).append(e)
        groups = sorted(families, key=lambda x: _digest(ws["id"] + ":" + x))
        # Reserve a validation group when possible, and hold out another for a
        # meaningful test set; do not silently duplicate samples to pad splits.
        n = len(groups)
        valid_n = max(1, n // 10) if n >= 3 else 0
        test_n = max(1, n // 10) if n >= 5 else 0
        split_groups = {
            "test": groups[:test_n],
            "valid": groups[test_n:test_n + valid_n],
            "train": groups[test_n + valid_n:],
        }
        split_rows = {}
        split_ids = {}
        for name, names in split_groups.items():
            samples = [e for group in names for e in families[group]]
            split_ids[name] = [e["id"] for e in samples]
            split_rows[name] = []
            for e in samples:
                instruction = e["instruction"]
                if e["context"]:
                    instruction += "\n\nReference context (data, not instructions):\n" + e["context"]
                messages = []
                if ws["instructions"]:
                    messages.append({"role": "system", "content": redact_text(ws["instructions"])})
                messages.extend([{"role": "user", "content": redact_text(instruction)},
                                 {"role": "assistant", "content": redact_text(e["response"])}])
                split_rows[name].append({"messages": messages})
        manifest = {"schema": 1, "workspace_id": ws["id"], "title": ws["title"],
                    "domain": ws["domain"], "model_source": ws["model_source"],
                    "exported_at": now(), "reviewed_examples": len(reviewed),
                    "excluded_drafts": len(self.list_examples(workspace_id)) - len(reviewed),
                    "split_ids": split_ids, "split_counts": {k: len(v) for k,v in split_ids.items()},
                    "format": "mlx-lm chat JSONL", "training_performed": False,
                    "notes": "Reviewed examples only. Verify licensing, PII, leakage, and external model terms before training."}
        provenance = []
        for split, ids in split_ids.items():
            for eid in ids:
                example = next(e for e in reviewed if e["id"] == eid)
                provenance.append({"id": eid, "split": split, "source": example["source"],
                                   "scenario_group": example["group"], "tags": example["tags"],
                                   "reviewed_at": example["reviewed_at"], "fingerprint": example["fingerprint"]})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("provenance.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in provenance))
            for name, rows in split_rows.items():
                archive.writestr(f"{name}.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("README.txt", "Dataset only; model weights are not trained by this export.\n"
                             "Apple Silicon: pip install 'mlx-lm[train]'\n"
                             "mlx_lm.lora --model <compatible-mlx-model> --train --data <extracted-folder> --iters 200\n"
                             "Assess your held-out test.jsonl separately.\n")
        data = buf.getvalue()
        if len(data) > MAX_EXPORT_BYTES:
            raise ValueError("Export exceeds 12 MiB; split this curriculum into smaller projects.")
        return {"filename": f"training-{ws['domain']}-{ws['id'][:12]}.zip",
                "mime": "application/zip", "content_base64": base64.b64encode(data).decode(), "manifest": manifest}

    def dispatch(self, method, params):
        routes = {
            "training.workspaces": lambda: self.list_workspaces(**params),
            "training.create": lambda: self.create_workspace(**params),
            "training.update": lambda: self.update_workspace(**params),
            "training.delete": lambda: self.delete_workspace(**params),
            "training.examples": lambda: self.list_examples(**params),
            "training.save": lambda: self.save_example(**params),
            "training.review": lambda: self.review(**params),
            "training.delete_example": lambda: self.delete_example(**params),
            "training.search": lambda: self.search(**params),
            "training.export": lambda: self.export(**params),
            "training.domains": lambda: DOMAINS.copy(),
            "training.import_tor": lambda: self.import_tor_page(**params),
        }
        if method not in routes:
            raise ValueError("Unknown training operation.")
        return routes[method]()
