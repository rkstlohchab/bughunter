"""Project-scoped attack monitoring over access logs from any host.

A source is a log file or a folder of logs that the user exports, syncs or
mounts from their host (nginx/Apache on a server, a Vercel/Netlify log drain
written to disk, Cloudflare/AWS/GCP/Azure log exports). Each ingest reads only
new bytes, survives rotation, and merges detections into incidents keyed by
project, technique, client and day. Monitoring never sends traffic and never
changes the user's server; block rules are returned for the user to apply.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import threading
import time
from collections import Counter
from pathlib import Path

from ..store import now
from . import detectors
from .parsers import detect_format, parse, parser_for

LOG_SUFFIXES = (".log", ".txt", ".json", ".jsonl", ".ndjson", ".gz", ".csv", ".tsv")
MAX_BYTES_PER_INGEST = 64 * 1024 * 1024
MAX_FILES = 200
MIN_INTERVAL = 15


def _incident_id(project_id: str, technique: str, ip: str, day: str) -> str:
    return "incident_" + hashlib.sha256(f"{project_id}|{technique}|{ip}|{day}".encode()).hexdigest()[:24]


class SiteMonitor:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ sources

    def add_source(self, project_id: str, path: str, name: str | None = None, watch: bool = False,
                   interval_s: int = 60) -> dict:
        if not self.store.get("project", str(project_id)):
            raise ValueError("Choose an existing project for this website.")
        location = Path(os.path.expanduser(str(path))).resolve()
        if not location.exists():
            raise ValueError(f"Log path not found: {location}")
        if not (location.is_file() or location.is_dir()):
            raise ValueError("Use a log file or a folder of log files.")
        if type(watch) is not bool or type(interval_s) is not int or not MIN_INTERVAL <= interval_s <= 86400:
            raise ValueError(f"watch must be true/false and interval_s {MIN_INTERVAL}..86400.")
        for existing in self.store.list("monitor_source", project_id=project_id):
            if existing["path"] == str(location):
                return self.store.put("monitor_source", {**existing, "watch": watch, "interval_s": interval_s}, id=existing["id"])
        source = self.store.put("monitor_source", {
            "project_id": project_id, "path": str(location), "name": (name or location.name)[:120], "watch": watch,
            "interval_s": interval_s, "files": {}, "format": None, "w3c_header": None, "last_ingest": None,
            "last_error": None, "events_total": 0, "unparsed_total": 0})
        if watch:
            self._ensure_thread()
        return source

    def sources(self, project_id: str) -> list[dict]:
        return [{k: v for k, v in s.items() if k != "files"} | {"files_tracked": len(s.get("files", {}))}
                for s in self.store.list("monitor_source", project_id=project_id)]

    def remove_source(self, source_id: str) -> dict:
        removed = self.store.delete("monitor_source", source_id)
        return {"removed": removed}

    def _files(self, location: Path) -> list[Path]:
        if location.is_file():
            return [location]
        rows = [p for p in location.rglob("*") if p.is_file() and p.suffix.lower() in LOG_SUFFIXES]
        return sorted(rows, key=lambda p: p.stat().st_mtime)[-MAX_FILES:]

    # ------------------------------------------------------------ ingest

    def _read_new(self, path: Path, state: dict, budget: int) -> tuple[list[str], dict]:
        stat = path.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
        if path.suffix.lower() == ".gz":
            if state.get("identity") == identity and state.get("size") == stat.st_size:
                return [], state
            with gzip.open(path, "rb") as handle:
                data = handle.read(budget)
            return data.decode("utf-8", "replace").splitlines(), {"identity": identity, "size": stat.st_size, "offset": stat.st_size}
        offset = state.get("offset", 0) if state.get("identity") == identity and stat.st_size >= state.get("offset", 0) else 0
        if offset >= stat.st_size:
            return [], {"identity": identity, "size": stat.st_size, "offset": offset}
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(budget)
        end = data.rfind(b"\n")
        if end < 0 and offset + len(data) < stat.st_size:
            return [], {"identity": identity, "size": stat.st_size, "offset": offset}
        complete = data if end < 0 else data[:end + 1]
        return complete.decode("utf-8", "replace").splitlines(), {"identity": identity, "size": stat.st_size,
                                                                  "offset": offset + len(complete)}

    def ingest(self, source_id: str) -> dict:
        with self._lock:
            source = self.store.get("monitor_source", source_id)
            if not source:
                raise ValueError("Unknown monitor source.")
            location = Path(source["path"])
            if not location.exists():
                source.update(last_error="Log path no longer exists.", last_ingest=now())
                self.store.put("monitor_source", source, id=source_id)
                raise ValueError(source["last_error"])
            budget, lines = MAX_BYTES_PER_INGEST, []
            files = dict(source.get("files") or {})
            for path in self._files(location):
                if budget <= 0:
                    break
                fresh, state = self._read_new(path, files.get(str(path), {}), budget)
                files[str(path)] = state
                budget -= sum(len(line) + 1 for line in fresh)
                lines.extend(fresh)
            header = next((line for line in lines if line.startswith("#Fields:")), None) or source.get("w3c_header")
            if source.get("format") is None and lines:
                name, _ = detect_format(lines)
                source["format"] = name
            if header:
                source["w3c_header"] = header
            events, unparsed = [], 0
            if source.get("format"):
                parser = parser_for(source["format"], source.get("w3c_header"))
                for line in lines:
                    if not line.strip() or line.startswith("#"):
                        continue
                    event = parse(parser, line)
                    if event is None:
                        unparsed += 1
                    else:
                        events.append(event)
            incidents = self._record(source["project_id"], source_id, events)
            source.update(files=files, last_ingest=now(), last_error=None,
                          events_total=source.get("events_total", 0) + len(events),
                          unparsed_total=source.get("unparsed_total", 0) + unparsed)
            self.store.put("monitor_source", source, id=source_id)
            return {"source_id": source_id, "format": source.get("format"), "lines": len(lines), "events": len(events),
                    "unparsed": unparsed, "incidents_updated": len(incidents),
                    "incidents": [self._brief(i) for i in incidents[:20]],
                    "budget_exhausted": budget <= 0,
                    "coverage": "Request lines only; POST bodies are visible only when the host or WAF logs them."}

    def analyze_events(self, events: list[dict]) -> list[dict]:
        """Pure detection: list of raw detections (technique, ip, events, detail)."""
        found = []
        for event in events:
            for technique in detectors.signatures(event):
                found.append((technique, event["ip"], [event], {}))
        found.extend(detectors.behaviours(events))
        return found

    def _record(self, project_id: str, source_id: str, events: list[dict]) -> list[dict]:
        grouped: dict[tuple[str, str, str], dict] = {}
        for technique, ip, hits, detail in self.analyze_events(events):
            day = (hits[0].get("time") or now())[:10]
            entry = grouped.setdefault((technique, ip, day), {"hits": [], "detail": {}})
            entry["hits"].extend(hits)
            entry["detail"].update(detail)
        saved = []
        for (technique, ip, day), entry in grouped.items():
            title, section, severity = detectors.TITLES[technique]
            hits = entry["hits"]
            successes = [e for e in hits if detectors._successful(e)]
            if successes and technique in {"sensitive_file_probe", "path_traversal", "sql_injection", "command_injection",
                                           "template_injection", "ssrf_attempt"}:
                severity = "critical" if technique in {"sensitive_file_probe", "path_traversal", "command_injection"} else "high"
            ident = _incident_id(project_id, technique, ip, day)
            existing = self.store.get("monitor_incident", ident) or {}
            statuses = Counter(existing.get("statuses", {}))
            statuses.update(str(e.get("status")) for e in hits)
            paths = Counter(existing.get("top_paths", {}))
            paths.update(e["path"] for e in hits)
            times = [t for t in (e.get("time") for e in hits) if t] + [t for t in (existing.get("first_seen"), existing.get("last_seen")) if t]
            samples = (existing.get("samples", []) + [detectors.sample(e) for e in (successes or hits)[:5]])[-8:]
            previous = existing.get("severity", "info")
            record = {
                "project_id": project_id, "source_id": source_id, "technique": technique, "title": title,
                "tbhm_section": section, "ip": ip, "day": day,
                "severity": max(previous, severity, key=lambda s: detectors.SEVERITY_ORDER[s]),
                "count": existing.get("count", 0) + len(hits),
                "possibly_successful": existing.get("possibly_successful", 0) + len(successes),
                "first_seen": min(times) if times else None, "last_seen": max(times) if times else None,
                "statuses": dict(statuses), "top_paths": dict(paths.most_common(10)), "samples": samples,
                "user_agents": list(dict.fromkeys(existing.get("user_agents", []) + [e.get("ua") for e in hits[:3] if e.get("ua")]))[:5],
                "detail": {**existing.get("detail", {}), **entry["detail"]},
                "fix": detectors.GUIDANCE[technique],
                "block_rules": detectors.block_rules(technique, ip, [p for p, _ in paths.most_common(3)]),
                "status": existing.get("status", "open"),
            }
            saved.append(self.store.put("monitor_incident", record, id=ident))
        if saved:
            worst = max(saved, key=lambda i: detectors.SEVERITY_ORDER[i["severity"]])
            self.store.event("site_monitor.incidents", {"project_id": project_id, "count": len(saved),
                                                         "worst": worst["severity"], "title": worst["title"]})
        return sorted(saved, key=lambda i: (-detectors.SEVERITY_ORDER[i["severity"]], -i["count"]))

    # ------------------------------------------------------------ reporting

    @staticmethod
    def _brief(incident: dict) -> dict:
        return {k: incident.get(k) for k in ("id", "title", "severity", "ip", "count", "possibly_successful", "last_seen",
                                              "tbhm_section", "status")}

    def incidents(self, project_id: str, min_severity: str = "low", limit: int = 50) -> list[dict]:
        if min_severity not in detectors.SEVERITY_ORDER:
            raise ValueError("min_severity must be info, low, medium, high or critical.")
        floor = detectors.SEVERITY_ORDER[min_severity]
        rows = [i for i in self.store.list("monitor_incident", project_id=project_id)
                if detectors.SEVERITY_ORDER[i["severity"]] >= floor]
        rows.sort(key=lambda i: (detectors.SEVERITY_ORDER[i["severity"]], i.get("possibly_successful") or 0,
                                 i.get("last_seen") or ""), reverse=True)
        return rows[:max(1, min(int(limit), 200))]

    def summary(self, project_id: str) -> dict:
        rows = self.store.list("monitor_incident", project_id=project_id)
        open_rows = [i for i in rows if i.get("status") == "open"]
        return {"project_id": project_id, "sources": self.sources(project_id),
                "open_incidents": len(open_rows),
                "by_severity": dict(Counter(i["severity"] for i in open_rows)),
                "by_technique": dict(Counter(i["technique"] for i in open_rows).most_common()),
                "possibly_successful": sum(1 for i in open_rows if i.get("possibly_successful")),
                "top_sources": [ip for ip, _ in Counter({i["ip"]: i["count"] for i in open_rows}).most_common(10)],
                "latest": [self._brief(i) for i in self.incidents(project_id, "low", 10)]}

    def resolve(self, incident_id: str, status: str, note: str | None = None) -> dict:
        if status not in {"open", "fixed", "blocked", "false_positive", "accepted"}:
            raise ValueError("status must be open, fixed, blocked, false_positive or accepted.")
        incident = self.store.get("monitor_incident", incident_id)
        if not incident:
            raise ValueError("Unknown incident.")
        return self.store.put("monitor_incident", {**incident, "status": status, "note": (note or "")[:2000]}, id=incident_id)

    # ------------------------------------------------------------ watching

    def _ensure_thread(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="site-monitor", daemon=True)
                self._thread.start()

    def _loop(self):
        while not self._stop.wait(5):
            if self.store.setting("emergency_stop", False):
                continue
            current = time.time()
            for source in self.store.list("monitor_source", watch=True):
                last = source.get("_last_poll", 0)
                if current - last < source.get("interval_s", 60):
                    continue
                self.store.put("monitor_source", {**source, "_last_poll": current}, id=source["id"])
                try:
                    self.ingest(source["id"])
                except Exception as exc:  # recorded on the source; the loop keeps watching others
                    latest = self.store.get("monitor_source", source["id"])
                    if latest:
                        self.store.put("monitor_source", {**latest, "last_error": str(exc)[:300]}, id=source["id"])

    def start_watchers(self):
        if any(self.store.list("monitor_source", watch=True)):
            self._ensure_thread()

    def shutdown(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def dispatch(self, method: str, params: dict):
        if method == "monitor.sources.add":
            self.store.require_desktop_owner()
            return self.add_source(**params)
        if method == "monitor.sources.list":
            return self.sources(params["project_id"])
        if method == "monitor.sources.remove":
            self.store.require_desktop_owner()
            return self.remove_source(params["source_id"])
        if method == "monitor.ingest":
            return self.ingest(params["source_id"])
        if method == "monitor.incidents":
            return self.incidents(params["project_id"], params.get("min_severity", "low"), params.get("limit", 50))
        if method == "monitor.summary":
            return self.summary(params["project_id"])
        if method == "monitor.resolve":
            return self.resolve(params["incident_id"], params["status"], params.get("note"))
        raise ValueError("Unknown site monitor method.")
