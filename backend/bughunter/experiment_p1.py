"""P1 evidence-backed skill experiments.

Only bundled test fixtures execute. Never evaluate commands, URLs or paths from
imported courses; a course is untrusted data, even when it contains instructions.
Lab results are evidence for a *mechanism*, not proof that a quoted procedure
works on an arbitrary external target.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any

from .store import new_id, now

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 90
MAX_CAPTURE_BYTES = 40_000
# Fixed recipes are intentionally independent of source-authored commands.
RECIPES = {
    "source_fidelity": {
        "title": "Check the original lesson",
        "description": "Confirm that every extracted step still matches the original indexed passage.",
        "level": "source_verified_only",
        "timeout": 15,
    },
    "memory_restart_lab": {
        "title": "Reproduce the memory-restart lab",
        "description": "Launch two temporary offline game processes and verify memory pointers and entity reads.",
        "level": "lab_reproduced_only",
        "timeout": 70,
    },
    "checkout_integrity_lab": {
        "title": "Reproduce the checkout lab",
        "description": "Run the bundled local checkout regression tests with no remote checkout traffic.",
        "level": "lab_reproduced_only",
        "timeout": 90,
    },
}
_PATTERN_RECIPES = (
    (re.compile(r"\b(?:memory|pointer|restart|health|entity|esp|game)\b", re.I), "memory_restart_lab"),
    (re.compile(r"\b(?:checkout|price|coupon|discount|cart|payment|quote)\b", re.I), "checkout_integrity_lab"),
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_excerpt(value: str) -> str:
    """Avoid writing arbitrary tool output or sensitive strings to the evidence DB."""
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", value)
    text = re.sub(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text[:MAX_CAPTURE_BYTES]


class ExperimentP1:
    def __init__(self, knowledge, run_workers: bool = True):
        self.knowledge = knowledge
        self.store = knowledge.store
        self._lock = threading.RLock()
        self._workers: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancels: set[str] = set()
        if run_workers:
            # Do not secretly replay interrupted work; it may have used local resources.
            for entry in self.store.list("experiment_run"):
                if entry.get("status") in {"queued", "running"}:
                    self._update(entry["id"], status="interrupted", finished_at=now(),
                                 error="Application restarted before the result was observed. Retry explicitly.")

    def _update(self, id: str, **changes):
        item = self.store.get("experiment_run", id)
        if not item:
            raise ValueError("Experiment not found.")
        return self.store.put("experiment_run", {**item, **changes})

    def available(self, procedure_id: str) -> list[dict]:
        procedure = self.knowledge.procedures.get(procedure_id)
        text = " ".join([procedure.get("title", "")] + [str(s.get("quote", "")) for s in procedure.get("steps", [])])
        names = ["source_fidelity"]
        names.extend(name for regex, name in _PATTERN_RECIPES if regex.search(text))
        return [{"id": name, **RECIPES[name]} for name in dict.fromkeys(names)]

    def _procedure(self, id):
        procedure = self.knowledge.procedures.get(id)
        source = self.store.get("source", procedure["source_id"])
        if source is None:
            raise ValueError("The cited source has been deleted.")
        return procedure, source

    def start(self, procedure_id: str, recipe: str, approve_lab: bool = False) -> dict:
        procedure, source = self._procedure(procedure_id)
        permitted = {r["id"] for r in self.available(procedure_id)}
        if recipe not in permitted or recipe not in RECIPES:
            raise ValueError("This procedure does not support that experiment recipe.")
        if recipe != "source_fidelity" and approve_lab is not True:
            raise ValueError("Confirm that you want to run the bundled disposable lab.")
        record = self.store.put("experiment_run", {
            "procedure_id": procedure_id, "source_id": procedure["source_id"],
            "project_id": procedure.get("project_id"), "recipe": recipe,
            "title": RECIPES[recipe]["title"], "status": "queued", "started_at": None,
            "finished_at": None, "verification_level": "not_tested", "result": None,
            "error": None, "observations": [], "hypotheses": [],
            "original_procedure_status": procedure.get("status", "candidate"),
            "source_sha256": source.get("content_hash") or source.get("sha256") or source.get("file_sha256"),
            "provenance": "bundled_test_fixture" if recipe != "source_fidelity" else "local_source_index",
        }, id=new_id("experiment"))
        thread = threading.Thread(target=self.run, args=(record["id"],), daemon=True,
                                  name="experiment-" + record["id"][-8:])
        with self._lock:
            self._workers[record["id"]] = thread
        thread.start()
        return record

    def check_source(self, source_id: str, limit: int = 30) -> dict:
        """Launch citation-only checks across the lesson. No code from lessons runs."""
        if not self.store.get('source', source_id):
            raise ValueError('Source not found.')
        existing = self.list(limit=500)
        active = {e.get('procedure_id') for e in existing if
                  e.get('status') in {'queued','running','stopping'} and
                  e.get('recipe') == 'source_fidelity'}
        procedures = self.knowledge.procedures.list(source_id=source_id, limit=500)
        started=[]
        for p in procedures[:max(1,min(30,int(limit)))]:
            if p['id'] not in active:
                started.append(self.start(p['id'],'source_fidelity')['id'])
        return {'source_id':source_id,'started':len(started),'experiment_ids':started,
                'note':'Only citations and original source text were checked, not course commands.'}

    def insights(self, project_id: str = '*') -> list[dict]:
        """Aggregate recurring failures while retaining each primary evidence run."""
        groups: dict[tuple[str,str],dict] = {}
        for e in self.list(project_id=project_id,limit=500):
            if e.get('status') not in {'failed','interrupted','cancelled'}: continue
            label = e.get('error') or e['status']
            key = (e.get('recipe','unknown'),label)
            if key not in groups:
                groups[key]={'recipe':key[0],'issue':label,'count':0,'run_ids':[],
                             'suggested_checks':e.get('hypotheses') or []}
            groups[key]['count']+=1
            groups[key]['run_ids'].append(e['id'])
        return sorted(groups.values(),key=lambda x:-x['count'])[:30]

    def list(self, procedure_id: str | None = None, project_id: str = "*", limit: int = 100):
        entries = self.store.list("experiment_run")
        if procedure_id:
            entries = [e for e in entries if e.get("procedure_id") == procedure_id]
        if project_id != "*":
            entries = [e for e in entries if e.get("project_id") in (None, project_id)]
        return entries[:max(1, min(500, int(limit)))]

    def get(self, id: str):
        record = self.store.get("experiment_run", id)
        if not record:
            raise ValueError("Experiment not found.")
        return record

    def stop(self, id: str):
        with self._lock:
            row = self.get(id)
            if row["status"] not in {"queued", "running", "stopping"}:
                return row
            self._cancels.add(id)
            proc = self._processes.get(id)
            if proc and proc.poll() is None:
                # A process group ensures the disposable lab's child helpers stop too.
                if os.name == 'posix':
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
            return self._update(id, status="stopping")

    def retry(self, id: str, approve_lab: bool = False):
        old = self.get(id)
        if old["status"] in {"queued", "running", "stopping"}:
            raise ValueError("This experiment has not finished.")
        return self.start(old["procedure_id"], old["recipe"], approve_lab=approve_lab)

    def _check_cited_source(self, procedure):
        steps = procedure.get("steps") or []
        if not steps:
            raise ValueError("Procedure has no cited steps to check.")
        observations = []
        with self.store.connection() as db:
            for step in steps:
                row = db.execute("SELECT source_id,text,original_text,locator,modality FROM knowledge_chunks WHERE id=?",
                                 (step.get("chunk_id"),)).fetchone()
                matches = bool(row and row["source_id"] == procedure["source_id"]
                               and row["locator"] == step.get("locator")
                               and step.get("quote", "") in row["text"]
                               and step.get("quote", "") in row["original_text"])
                observations.append({
                    "step_id": step.get("id"), "chunk_id": step.get("chunk_id"),
                    "locator": step.get("locator"), "quote_sha256": _hash(step.get("quote", "")),
                    "matched_original": matches, "modality": step.get("modality"),
                })
        passed = all(s["matched_original"] for s in observations)
        return {"passed": passed, "observations": observations,
                "error": None if passed else "At least one cited step no longer matches its original source.",
                "hypotheses": [] if passed else ["Re-import or rebuild the cited procedure; the source passage may have changed."]}

    @staticmethod
    def _lab_command(recipe: str):
        py = ROOT / ".venv" / "bin" / "python"
        executable = str(py if py.is_file() else Path(sys.executable))
        if recipe == "memory_restart_lab":
            return [executable, str(ROOT / "scripts" / "verify_game_memory_restarts.py")]
        if recipe == "checkout_integrity_lab":
            return [executable, "-m", "pytest", "-q", str(ROOT / "backend" / "tests" / "test_commerce_audit.py")]
        raise ValueError("Unrecognized bundled lab.")

    def _run_lab(self, id, recipe):
        command = self._lab_command(recipe)
        proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                     "PYTHONPATH": str(ROOT / "backend"), "HOME": str(self.store.root)},
                                start_new_session=True)
        with self._lock:
            self._processes[id] = proc
            if id in self._cancels and proc.poll() is None:
                if os.name == 'posix':
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
        try:
            try:
                output, _ = proc.communicate(timeout=RECIPES[recipe]["timeout"])
            except subprocess.TimeoutExpired:
                proc.kill()
                output, _ = proc.communicate(timeout=5)
                return {"passed": False, "error": "Bundled experiment exceeded its time limit.",
                        "observations": [{"exit_code": proc.returncode, "output_sha256": hashlib.sha256(output).hexdigest(),
                                         "output_excerpt": _safe_excerpt(output.decode("utf-8", "replace"))}],
                        "hypotheses": ["Check whether the local test helper is unavailable or blocked by macOS access controls."]}
            detail = output.decode("utf-8", "replace")
            passed = proc.returncode == 0 and bool(detail.strip())
            return {"passed": passed, "observations": [{
                        "exit_code": proc.returncode, "output_sha256": hashlib.sha256(output).hexdigest(),
                        "output_excerpt": _safe_excerpt(detail), "command_id": recipe}],
                    "error": None if passed else "Bundled experiment failed; review its exit code and log.",
                    "hypotheses": [] if passed else ["Review the recorded test output and required local runtime dependencies."]}
        finally:
            with self._lock:
                self._processes.pop(id, None)

    def run(self, id: str):
        row = self.get(id)
        with self._lock:
            if id in self._cancels:
                self._update(id, status="cancelled", verification_level="not_verified", finished_at=now())
                self._cancels.discard(id)
                self._workers.pop(id, None)
                return self.get(id)
            self._update(id, status="running", started_at=now())
        recipe = row["recipe"]
        try:
            procedure, _ = self._procedure(row["procedure_id"])
            result = (self._check_cited_source(procedure) if recipe == "source_fidelity"
                      else self._run_lab(id, recipe))
            with self._lock:
                cancelled = id in self._cancels
                status = "cancelled" if cancelled else "passed" if result["passed"] else "failed"
                level = RECIPES[recipe]["level"] if status == "passed" else "not_verified"
                if status in {"passed", "failed"}:
                    # Publish evidence before marking the run complete. Readers
                    # must never observe a completed status without its graph edge.
                    with self.store.connection(immediate=True) as db:
                        self.knowledge._node(db,id,"experiment_run",row["title"],
                            {"source_id":row["source_id"],"status":status,"verification_level":level})
                        self.knowledge._edge(db,row["procedure_id"],id,"tested_by_fixture",row["source_id"],None,
                            {"recipe":recipe,"status":status,"proof_of_target_procedure":False})
                updated = self._update(id, status=status, finished_at=now(),
                                       verification_level=level,
                                       result={"passed":bool(result["passed"]),"recipe":recipe},
                                       observations=result["observations"],
                                       error=result.get("error"), hypotheses=result["hypotheses"])
            self.store.event("experiment.completed", {"id":id,"status":status,"recipe":recipe})
            return updated
        except Exception as exc:
            return self._update(id, status="failed", finished_at=now(),
                                verification_level="not_verified", error=_safe_excerpt(str(exc))[:400],
                                hypotheses=["Inspect the failed environment or missing dependency; no result was verified."])
        finally:
            with self._lock:
                self._cancels.discard(id)
                self._workers.pop(id, None)
