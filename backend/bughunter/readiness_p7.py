"""P7 release readiness: local preflight and operator-started reproducible checks.

Checks never contact user-supplied targets. Test suites use exact, bundled scripts.
Status describes observations, NOT claims of end-to-end Mac verification.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .store import now, new_id
from .security import redact_text

ROOT = Path(__file__).resolve().parents[2]
SUITES = {
    "learning": ("P0–P6 learning regression", ["bash", "scripts/verify_p6_learning.sh"], 300),
    "web_lab": ("Checkout and price integrity regression", ["bash", "scripts/verify_staging_quote_sandbox.sh"], 180),
    "memory_lab": ("Two-process memory pointer lab (read-only)", ["python", "scripts/verify_game_memory_restarts.py"], 120),
    "chat_routing": ("Natural-language task router", ["node", "--test", "tests/unit/task-intent.test.mjs"], 90),
    "casebook": ("P10 evidence and timeline verification", ["bash", "scripts/verify_p10_evidence.sh"], 240),
}
REQUIRED_FILES = (
    "src-tauri/src/lib.rs", "src-tauri/src/mac_desktop.rs",
    "backend/bughunter/course_p0.py", "backend/bughunter/experiment_p1.py",
    "backend/bughunter/research_p2.py", "backend/bughunter/research_browser_p3.py",
    "backend/bughunter/learning_p4.py", "backend/bughunter/mission_p5.py",
    "backend/bughunter/skill_memory_p6.py", "backend/bughunter/burp_history.py",
    "scripts/verify_game_memory_restarts.py", "scripts/build_burp_bridge.sh",
    "scripts/verify_p6_learning.sh", "src/Chat.tsx", "src/SettingsView.tsx",
)
_SENSITIVE = re.compile(r"(?i)(bearer\s+\S+|(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+)")


def _redact(text: str, limit: int = 2800) -> str:
    # Output is shown only to desktop operator. Avoid persisting credentials.
    return redact_text(_SENSITIVE.sub("[REDACTED]", str(text or "")))[-limit:]


def _check(id: str, title: str, state: str, detail: str, next_step: str = "") -> dict:
    return {"id": id, "title": title, "state": state, "detail": detail, "next_step": next_step}


class ReleaseReadinessP7:
    def __init__(self, app, recover=True):
        self.app = app
        self.store = app.store
        self._guard = threading.RLock()
        self._active: dict[str, tuple[threading.Event, subprocess.Popen | None]] = {}
        # Crash recovery must not mark a formerly running test as successful.
        for row in self.store.list("p7_run") if recover else []:
            if row.get("status") in {"queued", "running"}:
                row.update(status="interrupted", finished_at=now(),
                           detail="Desktop restarted before the result was confirmed.")
                self.store.put("p7_run", row, id=row["id"])

    def preflight(self) -> dict:
        checks = []
        mac = platform.system() == "Darwin"
        checks.append(_check("mac", "Apple Silicon macOS", "ready" if mac and platform.machine() in {"arm64", "aarch64"} else "not_verified",
                             "Native Apple Silicon target" if mac and platform.machine() in {"arm64", "aarch64"} else
                             f"Current environment: {platform.system()} / {platform.machine()}",
                             "Run the full build and checks on your Apple Silicon Mac." if not mac else ""))
        missing = [f for f in REQUIRED_FILES if not (ROOT / f).is_file()]
        modules = ("bughunter.course_p0", "bughunter.experiment_p1", "bughunter.research_p2",
                   "bughunter.research_browser_p3", "bughunter.learning_p4",
                   "bughunter.mission_p5", "bughunter.skill_memory_p6", "bughunter.burp_history")
        available = all(importlib.util.find_spec(name) is not None for name in modules)
        source_state = "ready" if not missing and available else "packaged" if available else "blocked"
        checks.append(_check("source", "Integrated source modules", source_state,
                             "All source files and P0–P6 modules present." if source_state == "ready" else
                             "Python modules available in the packaged backend; source-tree files are not included." if available else
                             "Missing modules or source files: " + ", ".join(missing[:12])))
        for name, binary, purpose in (
            ("node", "node", "Frontend build and Chat routing"),
            ("npm", "npm", "Frontend dependencies"),
            ("cargo", "cargo", "Tauri Rust compilation"),
            ("swiftc", "swiftc", "Native Accessibility helper"),
            ("ffmpeg", "ffmpeg", "Video frames"),
            ("ffprobe", "ffprobe", "Video duration"),
            ("ollama", "ollama", "Local inference (optional)"),
            ("agent_desktop", "agent-desktop", "Vercel desktop controller (optional)"),
        ):
            path = shutil.which(binary)
            checks.append(_check(name, purpose, "installed" if path else "not_installed",
                                 "Executable located on PATH." if path else f"{binary} not found on PATH.",
                                 f"Install {binary} if this component is needed." if not path else ""))
        settings = self.app.settings.get()
        provider = str(settings.get("provider") or "")
        model = str(settings.get("model") or "")
        checks.append(_check("model", "Configured reasoning model", "configured" if provider and model else "blocked",
                             f"Provider: {provider or '(not set)'}; model configured: {'yes' if model else 'no'}. No connection was attempted.",
                             "Use Settings → Save & check connection to verify model reachability."))
        checks.append(_check("mac_control", "Native Mac control", "not_verified",
                             "Controller is present in source; OS permissions and actual app interaction have not been tested by this check.",
                             "Grant Accessibility permissions, then test a disposable app through Chat on your Mac."))
        checks.append(_check("burp", "Burp Professional bridge", "not_verified",
                             "Bundled Java bridge and history adapter exist; live Burp Scanner/extension status not asserted.",
                             "Build the bridge, load it into Burp Professional, then use Settings → Connect tools."))
        checks.append(_check("course", "Original course ingestion", "not_verified",
                             "P0 importer is present. This report does not ingest or evaluate your large course ZIP.",
                             "Import the original ZIP under Knowledge, inspect video timestamps and citations."))
        checks.append(_check("tor", "Tor research session", "not_verified",
                             "P3 queue is present. No real Tor session, CAPTCHA interaction, or live onion capture was attempted.",
                             "Use a verified source bookmark and test pause/resume in the app."))
        checks.append(_check("memory", "Live game memory bridge", "not_verified",
                             "Bundled disposable memory fixture can be run below; actual third-party games are not verified.",
                             "Run the read-only memory lab and inspect its result."))
        checks.append(_check("training", "Model weight training", "not_implemented",
                             "Reviewed dataset export exists, but this preflight does not fine-tune or evaluate model weights."))
        latest = {suite: next((r for r in self.store.list("p7_run") if r.get("suite") == suite), None)
                  for suite in SUITES}
        return {"phase": "P7", "checked_at": now(), "checks": checks,
                "suites": [{"id": sid, "title": spec[0], "available": self._suite_available(sid),
                            "detail": "Bundled source-tree script and interpreter available" if self._suite_available(sid) else
                                      "Run scripts/verify_p7_release.sh from the extracted project on your Mac.",
                            "last": latest[sid] and {
                    key: latest[sid].get(key) for key in ("id", "status", "finished_at", "exit_code")}}
                           for sid, spec in SUITES.items()],
                "interpretation": "Installed ≠ connected; source present ≠ live Mac verified. No external targets were contacted."}

    @staticmethod
    def _suite_available(suite: str) -> bool:
        if suite not in SUITES or getattr(sys, "frozen", False): return False
        command = SUITES[suite][1]
        executable = sys.executable if command[0] == "python" else shutil.which(command[0])
        if not executable: return False
        if suite == "chat_routing" and not (ROOT / "node_modules/typescript/package.json").is_file():
            return False
        return all((ROOT / arg).is_file() for arg in command[1:] if arg.startswith(("scripts/", "tests/")))

    def list_runs(self) -> list[dict]:
        return [self._public(row) for row in self.store.list("p7_run")[:30]]

    @staticmethod
    def _public(row: dict) -> dict:
        return {key: row.get(key) for key in ("id", "suite", "title", "status", "started_at", "finished_at",
                                              "exit_code", "duration_seconds", "output", "detail")}

    def start(self, suite: str, approved: bool) -> dict:
        if approved is not True:
            raise ValueError("Confirm running the selected local smoke test.")
        if suite not in SUITES:
            raise ValueError("Unknown verification suite.")
        self.store.require_desktop_owner()
        if not self._suite_available(suite):
            raise ValueError("This test needs the extracted project source and its runtime dependencies. Use scripts/verify_p7_release.sh on your Mac.")
        if self.store.setting("emergency_stop", False):
            raise ValueError("Emergency stop is active.")
        with self._guard:
            if self._active:
                raise ValueError("A verification suite is already running.")
            run = self.store.put("p7_run", {"suite": suite, "title": SUITES[suite][0], "status": "queued",
                                                 "started_at": now(), "output": "", "detail": ""}, id=new_id("p7"))
            cancel = threading.Event()
            self._active[run["id"]] = (cancel, None)
            threading.Thread(target=self._worker, args=(run["id"], suite, cancel), daemon=True).start()
            self.store.event("p7.started", {"id": run["id"], "suite": suite})
            return self._public(run)

    def _worker(self, run_id: str, suite: str, cancel: threading.Event):
        label, template, timeout = SUITES[suite]
        command = [sys.executable if token == "python" else token for token in template]
        began = time.monotonic()
        process = None
        output: list[str] = []
        status = "failed"
        exit_code = None
        detail = ""
        try:
            if cancel.is_set():
                status = "cancelled"
                return
            row = self.store.get("p7_run", run_id)
            if not row or row.get("status") != "queued":
                return
            row["status"] = "running"
            self.store.put("p7_run", row, id=run_id)
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, start_new_session=True,
                                       env={**os.environ, "PYTHONUNBUFFERED": "1"})
            with self._guard:
                self._active[run_id] = (cancel, process)
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                stdout, _ = process.communicate(timeout=5)
                status = "timed_out"
                detail = f"Stopped at the fixed {timeout}s timeout."
            output.append(_redact(stdout))
            exit_code = process.returncode
            if cancel.is_set():
                status = "cancelled"
            elif status != "timed_out":
                status = "passed" if exit_code == 0 else "failed"
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            detail = _redact(str(exc), 400)
        finally:
            if process and process.poll() is None:
                self._terminate(process)
            row = self.store.get("p7_run", run_id)
            if row and row.get("status") not in {"interrupted"}:
                row.update(status="cancelled" if cancel.is_set() else status, exit_code=exit_code,
                           output="\n".join(output)[-2800:], detail=detail,
                           duration_seconds=round(time.monotonic()-began, 2), finished_at=now())
                self.store.put("p7_run", row, id=run_id)
                self.store.event("p7.finished", {"id": run_id, "suite": suite, "status": row["status"]})
            with self._guard:
                self._active.pop(run_id, None)

    @staticmethod
    def _terminate(proc: subprocess.Popen):
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name == "posix": os.killpg(proc.pid, signal.SIGKILL)
                else: proc.kill()
            except (OSError, ProcessLookupError): pass

    def stop(self, id: str, approved: bool):
        if approved is not True: raise ValueError("Confirm stopping the local verification.")
        with self._guard:
            active = self._active.get(id)
            if not active: raise ValueError("No active verification with this ID.")
            cancel, proc = active
            cancel.set()
            if proc: self._terminate(proc)
        return {"id": id, "stopping": True}

    def export(self) -> dict:
        self.store.require_desktop_owner()
        report = {"schema": "bughunter-p7/v1", "generated_at": now(),
                  "preflight": self.preflight(), "runs": self.list_runs(), "probes": self.probes(),
                  "live_mac_verified": False,
                  "note": "This evidence contains only local preflight and scripted results. Operator Mac testing is separate."}
        path = self.store.root / "exports" / "p7-release-readiness.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(path)
        return {"path": str(path), "report": report}

    def probe(self, target: str, approved: bool) -> dict:
        """Small explicit connectivity probes. No scan, checkout, or billed generation."""
        if approved is not True:
            raise ValueError("Confirm checking this external or local integration.")
        self.store.require_desktop_owner()
        if target not in {"model", "burp", "tor", "media"}:
            raise ValueError("Unknown integration probe.")
        try:
            if target == "model":
                from .providers import probe_provider
                found = probe_provider(self.app.settings.get())
                ok = bool(found.get("ok"))
                note = str(found.get("detail", "No model status returned."))
                kind = str(found.get("checked") or "provider probe")
            elif target == "burp":
                found = self.app.burp_history.dispatch("burp.health", {})
                ok = bool(found.get("connected"))
                note = "Authenticated local Burp health endpoint responded." if ok else "Local Burp bridge did not confirm its connection."
                kind = "authenticated local extension"
            elif target == "tor":
                found = self.app.tor.status()
                ok = bool(found.get("connected"))
                note = "Local SOCKS5 handshake succeeded." if ok else "Local SOCKS5 service was not reachable."
                kind = "local proxy handshake"
            else:
                found = self.app.media.status()
                ok = isinstance(found, dict)
                note = "Media runtime returned status. Actual Qwen/Whisper inference was not tested."
                kind = "runtime metadata"
        except Exception as exc:
            ok = False
            kind = "connection attempt failed"
            note = f"Connection not confirmed ({type(exc).__name__}). Check that the local component is running and configured."
        result = self.store.put("p7_probe", {"target":target, "ok":ok,
            "checked":kind, "detail":_redact(note, 400), "checked_at":now()})
        self.store.event("p7.probed", {"target":target,"ok":ok})
        return {key: result.get(key) for key in ("id", "target", "ok", "checked", "detail", "checked_at")}

    def probes(self) -> list[dict]:
        return [{key: row.get(key) for key in ("id", "target", "ok", "checked", "detail", "checked_at")}
                for row in self.store.list("p7_probe")[:20]]

    def cancel_all(self):
        with self._guard:
            for event, process in list(self._active.values()):
                event.set()
                if process:
                    self._terminate(process)

    def shutdown(self):
        self.cancel_all()

    def dispatch(self, method: str, params: dict) -> dict | list:
        if method == "readiness.p7.preflight": return self.preflight()
        if method == "readiness.p7.runs": return self.list_runs()
        if method == "readiness.p7.probes": return self.probes()
        if method == "readiness.p7.probe": return self.probe(params.get("target", ""), params.get("approved") is True)
        if method == "readiness.p7.start": return self.start(params.get("suite", ""), params.get("approved") is True)
        if method == "readiness.p7.stop": return self.stop(params.get("id", ""), params.get("approved") is True)
        if method == "readiness.p7.export": return self.export()
        raise ValueError("Unknown P7 readiness operation.")
