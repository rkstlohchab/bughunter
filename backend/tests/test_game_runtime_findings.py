"""Findings from native receipts and the service/tool surface for engine mapping and live analysis."""
from __future__ import annotations

import os

import pytest

from bughunter.app import Application
from bughunter.game_runtime.findings import record_from_receipt
from bughunter.game_runtime.service import birth
from bughunter.store import Store

FP = "ab" * 32


def _op(**over):
    base = {"id": "op1", "kind": "read", "status": "succeeded", "session_id": "s1", "target_fingerprint": FP,
            "process_instance_id": "1:1",
            "result": {"findings": [{"kind": "class_layout", "title": "IL2CPP class Game.PlayerWeapon",
                                     "address": "abs:0x20001000", "value_type": None,
                                     "evidence": {"name_slot_offset": "0x10"}}]}}
    return {**base, **over}


def test_findings_only_from_succeeded_reads_and_validated(tmp_path):
    store = Store(tmp_path / "db")
    assert record_from_receipt(store, _op(status="failed")) == []
    assert record_from_receipt(store, _op(kind="write")) == []
    bad = _op(result={"findings": [{"kind": "class_layout", "title": "x", "address": "0x1"},
                                   {"kind": "nope", "title": "valid title", "address": "abs:0x1"}]})
    assert record_from_receipt(store, bad) == []
    event = store.events()[-1]
    assert event["type"] == "game_runtime.finding" and event["data"]["rejected"] == 2 and event["data"]["finding_ids"] == []


def test_findings_dedupe_promote_and_process_scope(tmp_path):
    store = Store(tmp_path / "db")
    first = record_from_receipt(store, _op())[0]
    assert first["status"] == "candidate" and first["seen_count"] == 1
    again = record_from_receipt(store, _op())[0]
    assert again["status"] == "candidate" and again["seen_count"] == 1      # same receipt is not new evidence
    second = record_from_receipt(store, _op(id="op2"))[0]
    assert second["id"] == first["id"] and second["status"] == "observed" and second["seen_count"] == 2
    other_process = record_from_receipt(store, _op(id="op3", process_instance_id="2:2"))[0]
    assert other_process["id"] != first["id"]
    module = _op(id="op4", result={"findings": [{"kind": "static_root", "title": "Unreal name block table slot",
                                                 "address": "mod:Game+0x800", "value_type": "u64", "evidence": {}}]})
    a = record_from_receipt(store, module)[0]
    b = record_from_receipt(store, {**module, "id": "op5", "process_instance_id": "3:3"})[0]
    assert a["id"] == b["id"] and b["status"] == "observed"                  # module roots survive restarts


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path, primary=False, native_credential="a" * 64)
    monkeypatch.setattr(a.operator, "_loop", lambda *args: None)
    a.dispatch("gamelab.native.bootstrap", {"credential_hex": "a" * 64})
    yield a
    a.close()


def _task(a, mode="auto"):
    return a.harness.start("Map the game with engine probes", grants=["app"], mode="game", execution_mode=mode,
                           web_search_mode="off")


class _Executor:
    def __init__(self, g, session):
        self.g, self.session = g, session
        self.value = g.broker.register_executor("ex", "local-controller", "helper:1", session["process_instance_id"], FP)

    def finish(self, operation_id, result, status="succeeded"):
        b, epoch = self.g.broker, self.value["executor_epoch"]
        op = b.claim("ex", epoch, operation_id=operation_id)
        op = b.dispatched(op["id"], "ex", epoch, op["attempt_id"], op["revision"])
        return self.g.private("gamelab.native.complete", {
            "credential_hex": "a" * 64, "operation_id": op["id"], "executor_id": "ex", "executor_epoch": epoch,
            "attempt_id": op["attempt_id"], "revision": op["revision"], "status": status,
            "result": result if status == "succeeded" else None,
            "error": None if status == "succeeded" else {"code": "x", "message": "x"}})["operation"]


def test_probe_receipt_records_findings_and_job_ids_are_task_owned(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    ex = _Executor(g, session)
    with pytest.raises(ValueError, match="no receipt"):
        g.engine_probe(session["id"], job_id="job-x", harness_id=t["id"])
    started = g.engine_probe(session["id"], probe="il2cpp_class", name="PlayerWeapon", harness_id=t["id"])
    op = g.broker.get_operation(started["operation_id"])
    assert op["kind"] == "read" and op["effect_class"] == "read_only"
    assert op["arguments"]["analysis_action"] == "probe" and op["arguments"]["name"] == "PlayerWeapon"
    ex.finish(op["id"], {"job_id": "job-1", "complete": False, "findings": []})
    resumed = g.engine_probe(session["id"], job_id="job-1", harness_id=t["id"])
    assert g.broker.get_operation(resumed["operation_id"])["arguments"] == {"analysis_action": "probe", "job_id": "job-1"}
    ex.finish(resumed["operation_id"], {"job_id": "job-1", "complete": True, "findings": _op()["result"]["findings"]})
    findings = g.findings(session["id"], harness_id=t["id"])
    assert [f["title"] for f in findings] == ["IL2CPP class Game.PlayerWeapon"]
    assert g.status(t["id"])["sessions"][0]["findings"][0]["status"] == "candidate"
    other = _task(app)
    with pytest.raises(PermissionError):
        g.engine_probe(session["id"], job_id="job-1", harness_id=other["id"])


def test_engine_detect_receipt_sets_session_engine(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    ex = _Executor(g, session)
    op = g.engine_detect(session["id"], harness_id=t["id"])
    ex.finish(op["operation_id"], {"engine": {"engine": "unity_il2cpp", "evidence": []}, "modules": []})
    assert g.status(t["id"])["sessions"][0]["engine"] == "unity_il2cpp"


def test_watch_repeats_windows_and_stops(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    ex = _Executor(g, session)
    with pytest.raises(ValueError):
        g.watch(session["id"], address="0x1000", size=99999, harness_id=t["id"])
    started = g.watch(session["id"], address="0x20008000", size=64, duration_s=1, repeat=3, harness_id=t["id"])
    ex.finish(started["operation_id"], {"watch": True, "samples": 10, "findings": []})
    g._pump_watches()
    schedule = g.store.get("game_watch_schedule", session["id"] + ":" + started["watch_id"])
    assert schedule["windows"] == 2 and schedule["enabled"] and schedule["last_operation_id"] != started["operation_id"]
    g.watch_stop(session["id"], started["watch_id"], harness_id=t["id"])
    ex.finish(schedule["last_operation_id"], {"watch": True, "samples": 10, "findings": []})
    g._pump_watches()
    final = g.store.get("game_watch_schedule", session["id"] + ":" + started["watch_id"])
    assert final["windows"] == 2 and not final["enabled"]


def test_watch_stops_on_failed_window(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    ex = _Executor(g, session)
    started = g.watch(session["id"], addresses=["0x20008000", "0x20008100"], repeat=5, harness_id=t["id"])
    ex.finish(started["operation_id"], None, status="failed")
    g._pump_watches()
    assert not g.store.get("game_watch_schedule", session["id"] + ":" + started["watch_id"])["enabled"]


def test_finding_promote_creates_mapping_and_plan_mode_is_read_only(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    ex = _Executor(g, session)
    op = g.watch(session["id"], address="0x20008000", size=64, harness_id=t["id"])
    behaviour = {"kind": "value_behavior", "title": "counter at 0x20008024", "address": "abs:0x20008024",
                 "value_type": "i32", "evidence": {"label": "counter"}}
    ex.finish(op["operation_id"], {"watch": True, "findings": [behaviour]})
    finding = g.findings(session["id"], harness_id=t["id"])[0]
    recipe = g.finding_promote(session["id"], finding["id"], name="Charged ammo", harness_id=t["id"])
    assert recipe["root"] == "abs:0x20008024" and recipe["value_type"] == "i32" and recipe["verification_status"] == "candidate"
    assert g.store.get("game_finding", finding["id"])["recipe_id"] == recipe["id"]

    from bughunter.operator_tools import OperatorTools
    plan = _task(app, "plan")
    names = {s["name"] for s in OperatorTools(app, plan["run_id"]).specs()}
    assert {"game_engine_probe", "game_watch", "game_findings", "game_metadata_search"} <= names
    assert "game_finding_promote" not in names
    plan_session = g.broker.create_session("target", plan["id"], birth(os.getpid()), FP)
    with pytest.raises(PermissionError, match="Plan mode"):
        g.finding_promote(plan_session["id"], finding["id"], name="x1", harness_id=plan["id"])


def test_metadata_search_requires_installed_target(app):
    t = _task(app)
    g = app.game_runtime
    session = g.broker.create_session("target", t["id"], birth(os.getpid()), FP)
    with pytest.raises(ValueError, match="installed game target"):
        g.metadata_search(session["id"], "ammo", harness_id=t["id"])
