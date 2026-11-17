"""Route-by-route source review for the user's own website, local or from a git host."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from ..store import now
from .routes import analyze

GIT_URL = re.compile(r"^https://(?:www\.)?(github\.com|gitlab\.com|bitbucket\.org)/[\w.-]+/[\w.-]+?(?:\.git)?/?$")
SEVERITY = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
MAX_ROUTES = 5000


class RouteReview:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def _checkout(self, source: str) -> tuple[Path, str | None, str]:
        if GIT_URL.match(source):
            target = Path(self.store.root) / "workspaces" / "route_review" / hashlib.sha256(source.encode()).hexdigest()[:16]
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            if (target / ".git").exists():
                result = subprocess.run(["git", "-C", str(target), "pull", "--ff-only", "--depth", "1"], capture_output=True,
                                        text=True, timeout=300, env=env)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(["git", "clone", "--depth", "1", "--single-branch", source, str(target)],
                                        capture_output=True, text=True, timeout=300, env=env)
            if result.returncode != 0:
                raise ValueError("git could not fetch the repository (private repos need your git credentials configured): "
                                 + (result.stderr or result.stdout)[-400:])
            commit = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True,
                                    timeout=30).stdout.strip() or None
            return target, commit, "git"
        if source.startswith(("http://", "https://", "git@")):
            raise ValueError("Use an https GitHub, GitLab or Bitbucket repository URL, or a local folder path.")
        root = Path(os.path.expanduser(source)).resolve()
        if not root.is_dir():
            raise ValueError(f"Folder not found: {root}")
        commit = None
        if (root / ".git").exists():
            commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                    timeout=30).stdout.strip() or None
        return root, commit, "local"

    def review(self, project_id: str, source: str) -> dict:
        if not self.store.get("project", str(project_id)):
            raise ValueError("Choose an existing project for this website.")
        if not isinstance(source, str) or not source.strip() or len(source) > 1000:
            raise ValueError("Give a local folder path or a repository URL.")
        root, commit, kind = self._checkout(source.strip())
        result = analyze(root)
        routes = [{"method": r.method, "path": r.path, "file": r.file, "line": r.line, "framework": r.framework,
                   "flags": r.flags, "risk": max((SEVERITY[f["severity"]] for f in r.flags), default=0)}
                  for r in result["routes"]][:MAX_ROUTES]
        flags = [f for r in routes for f in r["flags"]] + result["file_flags"]
        record = self.store.put("route_review", {
            "project_id": project_id, "source": source.strip(), "source_kind": kind, "root": str(root), "commit": commit,
            "reviewed_at": now(), "stats": result["stats"], "routes": routes, "file_flags": result["file_flags"],
            "by_severity": dict(Counter(f["severity"] for f in flags)), "by_kind": dict(Counter(f["kind"] for f in flags))})
        self.store.event("route_review.completed", {"project_id": project_id, "review_id": record["id"],
                                                     "routes": len(routes), "flags": len(flags)})
        return self.summary(record["id"])

    def summary(self, review_id: str) -> dict:
        record = self._record(review_id)
        risky = sorted((r for r in record["routes"] if r["flags"]), key=lambda r: -r["risk"])
        return {"review_id": record["id"], "project_id": record["project_id"], "source": record["source"],
                "commit": record["commit"], "root": record["root"], "stats": record["stats"],
                "by_severity": record["by_severity"], "by_kind": record["by_kind"],
                "frameworks": dict(Counter(r["framework"] for r in record["routes"])),
                "riskiest_routes": [{k: r[k] for k in ("method", "path", "file", "line")} |
                                    {"flags": [f["kind"] + ":" + f["severity"] for f in r["flags"]]} for r in risky[:25]],
                "file_flags": record["file_flags"][:20],
                "next": "Read each flagged handler (file:line) to confirm, then confirm impact on the live site with the "
                        "listed live_check inside the project scope. Static flags are leads, not findings."}

    def routes(self, review_id: str, min_severity: str = "low", kind: str | None = None, path_contains: str | None = None,
               limit: int = 50) -> dict:
        record = self._record(review_id)
        floor = SEVERITY.get(min_severity)
        if floor is None:
            raise ValueError("min_severity must be info, low, medium, high or critical.")
        rows = []
        for route in record["routes"]:
            flags = [f for f in route["flags"] if SEVERITY[f["severity"]] >= floor and (kind is None or f["kind"] == kind)]
            if (flags or floor == 0) and (not path_contains or path_contains.lower() in route["path"].lower()):
                rows.append({**route, "flags": flags})
        rows.sort(key=lambda r: -max((SEVERITY[f["severity"]] for f in r["flags"]), default=0))
        return {"review_id": review_id, "total": len(rows), "routes": rows[:max(1, min(int(limit), 500))]}

    def latest(self, project_id: str) -> dict | None:
        rows = sorted(self.store.list("route_review", project_id=project_id), key=lambda r: r.get("reviewed_at") or "")
        return self.summary(rows[-1]["id"]) if rows else None

    def _record(self, review_id: str) -> dict:
        record = self.store.get("route_review", str(review_id))
        if not record:
            raise ValueError("Unknown route review.")
        return record

    def dispatch(self, method: str, params: dict):
        if method == "routes.review":
            self.store.require_desktop_owner()
            return self.review(params["project_id"], params["source"])
        if method == "routes.summary":
            return self.summary(params["review_id"])
        if method == "routes.list":
            return self.routes(params["review_id"], params.get("min_severity", "low"), params.get("kind"),
                               params.get("path_contains"), params.get("limit", 50))
        if method == "routes.latest":
            return self.latest(params["project_id"])
        raise ValueError("Unknown route review method.")
