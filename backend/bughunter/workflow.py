"""Workflow engine — DAG of adapter invocations.

A workflow spec is a dict of ordered nodes with dependencies; each node
names an adapter and its args. Outputs flow into the asset DB. Branching
is done with predicate expressions over previous node outputs.

Workflows live as ``workflow`` entities and persist their runs as
``workflow_run`` entities. Pre-built workflows are shipped in
``backend/bughunter/resources/workflows/``.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .store import Store, new_id, now


class WorkflowEngine:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self._running: dict[str, threading.Thread] = {}
        self._stop_flags: dict[str, threading.Event] = {}
        self.resources = Path(__file__).resolve().parent / "resources" / "workflows"

    # dispatch ------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        verbs = {
            "workflow.list":       self.list,
            "workflow.list_runs":  self.list_runs,
            "workflow.get":        self.get,
            "workflow.save":       self.save,
            "workflow.delete":     self.delete,
            "workflow.start":      self.start,
            "workflow.stop":       self.stop,
            "workflow.status":     self.status,
            "workflow.templates":  self.templates,
        }
        fn = verbs.get(method)
        if not fn:
            raise ValueError(f"Unknown workflow method: {method}")
        return fn(**(params or {}))

    # entity CRUD ---------------------------------------------------------

    def list(self, **_) -> list[dict]:
        return self.store.list("workflow")

    def list_runs(self, **_) -> list[dict]:
        return self.store.list("workflow_run")

    def get(self, *, id: str, **_) -> dict | None:
        return self.store.get("workflow", id)

    def save(self, *, name: str, spec: dict, id: str | None = None, **_) -> dict:
        self.store.require_desktop_owner()
        _validate_spec(spec)
        wf_id = id or new_id("wf")
        record = {"id": wf_id, "name": name, "spec": spec, "updated_at": now()}
        self.store.put("workflow", record, id=wf_id)
        return record

    def delete(self, *, id: str, **_) -> dict:
        self.store.require_desktop_owner()
        self.store.delete("workflow", id)
        return {"deleted": id}

    def templates(self, **_) -> list[dict]:
        out = []
        if self.resources.exists():
            for p in sorted(self.resources.glob("*.json")):
                try:
                    data = json.loads(p.read_text())
                    out.append({"name": p.stem, **data})
                except Exception:
                    continue
        return out

    # execution -----------------------------------------------------------

    def start(self, *, workflow_id: str | None = None,
              spec: dict | None = None, inputs: dict | None = None,
              approved: bool = True, operator_mode: bool = True, **_) -> dict:
        self.store.require_desktop_owner()
        if spec is None:
            wf = self.get(id=workflow_id) if workflow_id else None
            if not wf:
                raise ValueError("Unknown workflow.")
            spec = wf["spec"]
            name = wf["name"]
        else:
            _validate_spec(spec)
            name = f"ad-hoc-{int(time.time())}"
        run_id = new_id("wfrun")
        record = {
            "id": run_id, "name": name, "status": "running",
            "spec": spec, "inputs": inputs or {},
            "operator_mode": bool(operator_mode),
            "nodes": {n["id"]: {"status": "pending", "output": None}
                      for n in spec["nodes"]},
            "started_at": now(),
        }
        self.store.put("workflow_run", record, id=run_id)
        stop = threading.Event()
        self._stop_flags[run_id] = stop
        thread = threading.Thread(target=self._execute,
                                  args=(run_id, spec, inputs or {}, stop,
                                        bool(operator_mode)),
                                  daemon=True, name=f"workflow:{run_id}")
        self._running[run_id] = thread
        thread.start()
        if hasattr(self.app, "timeline"):
            self.app.timeline.emit(kind="workflow", phase="start",
                                   run_id=run_id,
                                   payload={"name": name,
                                            "nodes": len(spec["nodes"])})
        return record

    def stop(self, *, id: str, **_) -> dict:
        flag = self._stop_flags.get(id)
        if flag:
            flag.set()
        record = self.store.get("workflow_run", id)
        if record:
            record = {**record, "status": "cancelled", "stopped_at": now()}
            self.store.put("workflow_run", record, id=id)
        return record or {"stopped": id}

    def status(self, *, id: str, **_) -> dict | None:
        return self.store.get("workflow_run", id)

    # engine --------------------------------------------------------------

    def _execute(self, run_id: str, spec: dict, inputs: dict,
                 stop: threading.Event, operator_mode: bool) -> None:
        state: dict[str, Any] = {"inputs": inputs, "nodes": {}}
        nodes_by_id: dict[str, dict] = {n["id"]: n for n in spec["nodes"]}
        pending = set(nodes_by_id)
        failed = False
        try:
            while pending and not stop.is_set():
                ready = [n for n in pending
                         if all(dep in state["nodes"]
                                for dep in nodes_by_id[n].get("depends_on", []))]
                if not ready:
                    raise RuntimeError("Workflow deadlock: cyclic or unsatisfied deps.")
                for node_id in ready:
                    if stop.is_set():
                        break
                    node = nodes_by_id[node_id]
                    if not _predicate_ok(node.get("when"), state):
                        state["nodes"][node_id] = {"status": "skipped",
                                                   "output": None}
                        pending.discard(node_id)
                        continue
                    self._mark(run_id, node_id, "running")
                    try:
                        output = self._run_node(node, state, operator_mode)
                        state["nodes"][node_id] = {"status": "ok", "output": output}
                        self._mark(run_id, node_id, "ok", output)
                    except Exception as exc:
                        state["nodes"][node_id] = {"status": "error",
                                                   "error": repr(exc)}
                        self._mark(run_id, node_id, "error", {"error": repr(exc)})
                        if node.get("required", True):
                            failed = True
                            break
                    pending.discard(node_id)
                if failed:
                    break
            status = ("cancelled" if stop.is_set()
                      else "failed" if failed
                      else "completed")
        except Exception as exc:
            status = "failed"
            self.store.event("workflow.error", {"id": run_id, "error": repr(exc)})
        record = self.store.get("workflow_run", run_id) or {}
        record = {**record, "status": status, "finished_at": now()}
        self.store.put("workflow_run", record, id=run_id)
        if hasattr(self.app, "timeline"):
            self.app.timeline.emit(kind="workflow", phase="complete",
                                   run_id=run_id, outcome=status)

    def _run_node(self, node: dict, state: dict, operator_mode: bool) -> Any:
        adapter_name = node.get("adapter")
        if not adapter_name:
            raise ValueError("node missing adapter")
        args = _resolve_refs(node.get("args", {}), state)
        tools = (self.app.operator_tools
                 if operator_mode and hasattr(self.app, "operator_tools")
                 else None)
        if tools is None:
            raise RuntimeError("workflow requires operator_mode=True")
        return tools.invoke_adapter(adapter_name, **args)

    def _mark(self, run_id: str, node_id: str, status: str,
              output: Any = None) -> None:
        record = self.store.get("workflow_run", run_id) or {}
        nodes = dict(record.get("nodes", {}))
        prev = nodes.get(node_id, {})
        nodes[node_id] = {**prev, "status": status, "output": output,
                          "updated_at": now()}
        record = {**record, "nodes": nodes, "updated_at": now()}
        self.store.put("workflow_run", record, id=run_id)


# --------------------------------------------------------------------------


def _validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict) or "nodes" not in spec:
        raise ValueError("workflow spec requires 'nodes'")
    ids = set()
    for node in spec["nodes"]:
        if "id" not in node or "adapter" not in node:
            raise ValueError("every node needs {id, adapter}")
        if node["id"] in ids:
            raise ValueError(f"duplicate node id: {node['id']}")
        ids.add(node["id"])
    for node in spec["nodes"]:
        for dep in node.get("depends_on", []):
            if dep not in ids:
                raise ValueError(f"unknown dep: {dep}")


def _predicate_ok(when: str | None, state: dict) -> bool:
    if not when:
        return True
    try:
        return bool(_eval_simple(when, state))
    except Exception:
        return False


def _eval_simple(expr: str, state: dict) -> bool:
    """Evaluate a tiny predicate language: 'nodes.X.status == "ok"' etc.
    Deliberately NOT ``eval()``. Only supports a safe subset.
    """
    # supported ops
    for op in (" == ", " != ", " contains "):
        if op in expr:
            left, right = expr.split(op, 1)
            lv = _resolve_path(left.strip(), state)
            rv = _lit(right.strip())
            if op == " == ":
                return lv == rv
            if op == " != ":
                return lv != rv
            if op == " contains ":
                return str(rv) in str(lv or "")
    return False


def _resolve_path(path: str, state: dict) -> Any:
    out = state
    for part in path.split("."):
        if isinstance(out, dict):
            out = out.get(part)
        else:
            return None
    return out


def _lit(s: str) -> Any:
    if s in {"true", "false"}:
        return s == "true"
    if s == "null":
        return None
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return s


def _resolve_refs(args: dict, state: dict) -> dict:
    """Replace ``$nodes.foo.output.bar`` references with real values."""
    def _r(v):
        if isinstance(v, str) and v.startswith("$"):
            return _resolve_path(v[1:], state)
        if isinstance(v, dict):
            return {k: _r(sub) for k, sub in v.items()}
        if isinstance(v, list):
            return [_r(x) for x in v]
        return v
    return {k: _r(v) for k, v in args.items()}
