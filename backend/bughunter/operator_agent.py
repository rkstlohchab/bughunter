"""Operator agent — parallel path with the OperatorTools surface.

This runs alongside the investigation ``RunManager``. Different surface,
different RPC namespace, different invariants. The investigation agent
is NOT reused — we deliberately do not import its ModelTools or gateway
to make it impossible for an operator run to accidentally touch the
investigation plumbing.

Approval model: the operator starts a session explicitly ("operator mode
on"). Within the session, the Rust shell shows every adapter / shell /
http / file_write call that the model proposes and the operator either
approves or denies. Auto-approve is optional per adapter class
(read-only enumeration adapters can be marked auto).

Transcripts, outputs, timeline events — all persisted. Nothing is sent
through the investigation gateway.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .store import Store, new_id, now
from .operator_tools import OperatorTools


OPERATOR_BASE_PROMPT = """\
You are the OPERATOR agent of the bughunter workbench.

- You have the unbounded tool surface described by OperatorTools.
- Every adapter / shell / http call you propose is logged and the operator
  approves or denies it on the desktop. You do not approve your own calls.
- Writes, exploits, and file modifications require `approved=true` — the
  operator sets that field by approving the call in the UI, not you.
- You can query the asset DB (assets_query) and reference stored vault
  entries by name using `{{VAULT:name}}` tokens inside string arguments.
- Record interesting findings as structured asset-DB inserts. Keep raw
  secrets out of chat; use vault names.
- When uncertain about a target or scope, ask the operator. Do not act on
  ambiguity.
"""


class OperatorAgent:
    def __init__(self, app):
        self.app = app
        self.store: Store = app.store
        self._runs: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}

    # dispatch ------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        verbs = {
            "operator.enabled":      self.enabled,
            "operator.enable":       self.enable,
            "operator.disable":      self.disable,
            "operator.start":        self.start,
            "operator.stop":         self.stop,
            "operator.resume":       self.resume,
            "operator.list":         self.list,
            "operator.get":          self.get,
            "operator.send":         self.send,
            "operator.tools":        self.tools_spec,
            "operator.invoke":       self.invoke_tool,
            "operator.pending_approvals": self.pending_approvals,
            "operator.approve_call": self.approve_call,
            "operator.set_auto_readonly": self.set_auto_readonly,
        }
        fn = verbs.get(method)
        if not fn:
            raise ValueError(f"Unknown operator method: {method}")
        return fn(**(params or {}))

    # mode toggle ---------------------------------------------------------

    def enabled(self, **_) -> bool:
        return bool(self.store.setting("operator_mode_enabled", False))

    def enable(self, *, approved: bool = False, **_) -> dict:
        if not approved:
            raise ValueError("Enabling operator mode requires explicit approval.")
        self.store.require_desktop_owner()
        self.store.set_setting("operator_mode_enabled", True)
        self.store.set_setting("operator_mode_enabled_at", now())
        self.store.event("operator.mode", {"state": "enabled"})
        return {"enabled": True}

    def disable(self, **_) -> dict:
        self.store.require_desktop_owner()
        self.store.set_setting("operator_mode_enabled", False)
        self.store.event("operator.mode", {"state": "disabled"})
        return {"enabled": False}

    # run lifecycle -------------------------------------------------------

    def start(self, *, prompt: str, objective: str | None = None,
              approved: bool = False, **_) -> dict:
        if not self.enabled():
            raise ValueError("Operator mode is off. Enable it from Settings.")
        if not approved:
            raise ValueError("Starting an operator run requires explicit approval.")
        self.store.require_desktop_owner()
        run_id = new_id("oprun")
        record = {
            "id": run_id, "kind": "operator",
            "prompt": prompt, "objective": objective or "",
            "status": "running",
            "transcript": [
                {"role": "system", "content": OPERATOR_BASE_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "turns_done": 0,
            "created_at": now(),
        }
        self.store.put("operator_run", record, id=run_id)
        stop = threading.Event()
        self._stops[run_id] = stop
        thread = threading.Thread(target=self._loop, args=(run_id, stop),
                                  daemon=True, name=f"operator:{run_id}")
        self._runs[run_id] = thread
        thread.start()
        if hasattr(self.app, "timeline"):
            self.app.timeline.emit(kind="model_turn", phase="start",
                                   run_id=run_id,
                                   payload={"objective": objective})
        return record

    def stop(self, *, id: str, **_) -> dict:
        flag = self._stops.get(id)
        if flag:
            flag.set()
        record = self.store.get("operator_run", id)
        if record:
            record = {**record, "status": "cancelled", "stopped_at": now()}
            self.store.put("operator_run", record, id=id)
        return record or {"stopped": id}

    def resume(self, *, id: str, **_) -> dict:
        record = self.store.get("operator_run", id)
        if not record or record.get("status") == "running":
            return record or {}
        record = {**record, "status": "running", "resumed_at": now()}
        self.store.put("operator_run", record, id=id)
        stop = threading.Event()
        self._stops[id] = stop
        thread = threading.Thread(target=self._loop, args=(id, stop),
                                  daemon=True, name=f"operator:{id}")
        self._runs[id] = thread
        thread.start()
        return record

    def send(self, *, id: str, message: str, **_) -> dict:
        record = self.store.get("operator_run", id)
        if not record:
            raise ValueError("Unknown operator run.")
        transcript = list(record.get("transcript", []))
        transcript.append({"role": "user", "content": message})
        record = {**record, "transcript": transcript,
                  "status": "running", "updated_at": now()}
        self.store.put("operator_run", record, id=id)
        if id not in self._runs or not self._runs[id].is_alive():
            self.resume(id=id)
        return record

    def list(self, **_) -> list[dict]:
        return self.store.list("operator_run")

    def get(self, *, id: str, **_) -> dict | None:
        return self.store.get("operator_run", id)

    # approvals -----------------------------------------------------------

    def pending_approvals(self, *, run_id: str | None = None, **_) -> list[dict]:
        all_pending = [a for a in self.store.list("operator_approval")
                       if a.get("status") == "pending"]
        if run_id:
            all_pending = [a for a in all_pending if a.get("run_id") == run_id]
        return all_pending

    def approve_call(self, *, id: str, approved: bool, **_) -> dict:
        """Approve or deny a queued operator tool call.

        If approved, the next ``_loop`` tick on that run picks the queued
        call and executes it.  If denied, the agent sees a 'denied' tool
        result in its transcript and continues.
        """
        self.store.require_desktop_owner()
        approval = self.store.get("operator_approval", id)
        if not approval:
            raise ValueError("Unknown approval.")
        if approval.get("status") != "pending":
            return approval
        approval = {**approval,
                    "status": "approved" if approved else "denied",
                    "decided_at": now()}
        self.store.put("operator_approval", approval, id=id)
        self.store.event("operator.approval_decided",
                         {"id": id, "status": approval["status"]})
        # if approved, execute it inline now (not on next tick) so the
        # operator sees the result immediately in the UI.
        if approved:
            try:
                tools = OperatorTools(self.app, run_id=approval["run_id"])
                name = approval["tool"]
                args = approval.get("args") or {}
                if name == "invoke_adapter":
                    output = tools.invoke_adapter(args["name"],
                                                  **(args.get("args") or {}))
                else:
                    fn = getattr(tools, name, None)
                    if not fn:
                        raise ValueError(f"unknown tool: {name}")
                    output = fn(**args)
                approval = {**approval, "output": _trunc_result(output)}
                self.store.put("operator_approval", approval, id=id)
            except Exception as exc:
                approval = {**approval, "status": "errored",
                            "error": repr(exc)}
                self.store.put("operator_approval", approval, id=id)
        # nudge the run forward
        run_id = approval["run_id"]
        record = self.store.get("operator_run", run_id) or {}
        if record.get("status") == "awaiting_approval":
            self.resume(id=run_id)
        return approval

    def set_auto_readonly(self, *, on: bool, **_) -> dict:
        self.store.require_desktop_owner()
        self.store.set_setting("operator_auto_readonly", bool(on))
        return {"operator_auto_readonly": bool(on)}

    # direct tool invocation (for UI "try a tool" buttons) -----------------

    def tools_spec(self, **_) -> list[dict]:
        return OperatorTools.TOOL_SPECS

    def invoke_tool(self, *, name: str, args: dict | None = None,
                    run_id: str | None = None, approved: bool = False, **_) -> Any:
        if not self.enabled():
            raise ValueError("Operator mode is off.")
        if not approved:
            raise ValueError("Direct tool invocation requires approval.")
        tools = OperatorTools(self.app, run_id=run_id)
        args = dict(args or {})
        if name == "invoke_adapter":
            return tools.invoke_adapter(args.pop("name"), **args.get("args", {}))
        if not hasattr(tools, name):
            raise ValueError(f"unknown tool: {name}")
        return getattr(tools, name)(**args)

    # core loop -----------------------------------------------------------

    def _loop(self, run_id: str, stop: threading.Event) -> None:
        """One run loop.

        Mirrors ``RunManager._api`` structure but uses the operator surface.
        """
        from . import provider_api  # late import to avoid circular
        record = self.store.get("operator_run", run_id) or {}
        tools = OperatorTools(self.app, run_id=run_id)
        settings = self.app.settings.get()
        max_turns = min(int(settings.get("max_agent_turns", 120) or 120), 300)
        try:
            while not stop.is_set():
                record = self.store.get("operator_run", run_id) or {}
                if record.get("status") not in {"running", "awaiting_approval"}:
                    break
                turn = record.get("turns_done", 0)
                if turn >= max_turns:
                    self._finish(run_id, "max_turns")
                    return
                transcript = list(record.get("transcript", []))
                # Call the provider for one turn
                try:
                    response = provider_api.single_tool_turn(
                        provider_cfg=settings,
                        transcript=transcript,
                        tool_specs=OperatorTools.TOOL_SPECS,
                    )
                except Exception as exc:
                    self._finish(run_id, "provider_error", error=repr(exc))
                    return
                assistant_msg = response.get("message")
                tool_calls = response.get("tool_calls") or []
                if assistant_msg is not None:
                    transcript.append(assistant_msg)
                if not tool_calls:
                    # final answer — stop here, operator can send more
                    self._update(run_id, transcript=transcript,
                                 status="awaiting_user", turns_done=turn + 1)
                    return
                # execute tool calls
                for call in tool_calls:
                    if stop.is_set():
                        break
                    name = call.get("name")
                    args = call.get("input") or {}
                    call_id = call.get("id") or new_id("tc")
                    # ALL operator tool calls need approval unless auto-approved
                    approved = self._is_auto_approved(name, args)
                    if not approved:
                        self._queue_approval(run_id, call_id, name, args)
                        self._update(run_id, transcript=transcript,
                                     status="awaiting_approval",
                                     turns_done=turn + 1)
                        return
                    try:
                        if name == "invoke_adapter":
                            output = tools.invoke_adapter(args["name"],
                                                         **(args.get("args") or {}))
                        else:
                            fn = getattr(tools, name, None)
                            if not fn:
                                raise ValueError(f"unknown tool: {name}")
                            output = fn(**args)
                        transcript.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": _trunc_result(output),
                        })
                    except Exception as exc:
                        transcript.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps({"error": repr(exc)}),
                        })
                self._update(run_id, transcript=transcript,
                             turns_done=turn + 1)
            # loop exited due to stop flag
            self._finish(run_id, "cancelled" if stop.is_set() else "idle")
        except Exception as exc:
            self._finish(run_id, "crashed", error=repr(exc))

    def _is_auto_approved(self, name: str, args: dict) -> bool:
        """Only certain enumeration adapters auto-run. Everything else parks."""
        if not self.store.setting("operator_auto_readonly", False):
            return False
        if name == "invoke_adapter":
            return args.get("name") in {"nmap", "httpx", "ffuf"} \
                   and not args.get("args", {}).get("write")
        return False

    def _queue_approval(self, run_id: str, call_id: str, name: str,
                        args: dict) -> None:
        approval = {
            "id": new_id("appr"),
            "run_id": run_id, "call_id": call_id,
            "tool": name, "args": args,
            "status": "pending", "created_at": now(),
        }
        self.store.put("operator_approval", approval, id=approval["id"])
        self.store.event("operator.approval_queued",
                         {"run": run_id, "tool": name})

    def _update(self, run_id: str, **fields) -> None:
        record = self.store.get("operator_run", run_id) or {}
        record = {**record, **fields, "updated_at": now()}
        self.store.put("operator_run", record, id=run_id)

    def _finish(self, run_id: str, status: str,
                error: str | None = None) -> None:
        record = self.store.get("operator_run", run_id) or {}
        record = {**record, "status": status, "finished_at": now()}
        if error:
            record["error"] = error
        self.store.put("operator_run", record, id=run_id)
        if hasattr(self.app, "timeline"):
            self.app.timeline.emit(kind="model_turn", phase="complete",
                                   run_id=run_id, outcome=status)


def _trunc_result(output: Any, limit: int = 20000) -> str:
    try:
        text = json.dumps(output, default=str)
    except Exception:
        text = str(output)
    if len(text) > limit:
        text = text[:limit] + f"\n... [truncated {len(text) - limit} chars]"
    return text
