"""Transactional, process-birth-bound native work ledger.

This module never launches or probes a process. The private service authenticates
requests and supplies observed helper-death/precondition evidence. Expired leases
retain ownership until that evidence arrives; unknown writes are never replayed.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable

from .contracts import FeatureRun, GameSession, NativeOperation, can_transition
from ..store import new_id, now


class BrokerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: str | dict) -> str:
    if isinstance(value, dict):
        value = _json(value)
    if not isinstance(value, str) or not value.strip():
        raise BrokerError("invalid_input", "A measured target fingerprint is required.")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerError("invalid_input", f"{name} must be a nonempty string.")
    return value


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 60:
        raise BrokerError("invalid_input", "Lease duration must be greater than zero and at most 60 seconds.")
    return float(value)


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


TERMINAL = frozenset({"succeeded", "failed", "outcome_unknown", "cancelled_before_dispatch"})
ACTIVE = frozenset({"claimed", "dispatched", "cancellation_requested"})


class GameRuntimeBroker:
    def __init__(self, store, *, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        # Individual DDL statements preserve the immediate transaction (unlike
        # sqlite3.executescript, which commits its caller's transaction).
        statements = (
            "CREATE TABLE IF NOT EXISTS gr_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
            "INSERT OR IGNORE INTO gr_meta VALUES ('global_stop_generation',0)",
            "CREATE TABLE IF NOT EXISTS gr_sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL, fingerprint TEXT NOT NULL, global_generation INTEGER NOT NULL, quiesced INTEGER NOT NULL DEFAULT 0, stop_deadline REAL)",
            "CREATE TABLE IF NOT EXISTS gr_executors (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, machine_id TEXT NOT NULL, helper_birth TEXT NOT NULL, process_birth TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, lease_until REAL NOT NULL, lease_seconds REAL NOT NULL, death_evidence TEXT)",
            "CREATE INDEX IF NOT EXISTS gr_executor_process ON gr_executors(machine_id,process_birth,state)",
            "CREATE TABLE IF NOT EXISTS gr_features (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES gr_sessions(id), data TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, stopped INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS gr_owners (process_birth TEXT PRIMARY KEY, feature_id TEXT NOT NULL REFERENCES gr_features(id))",
            "CREATE TABLE IF NOT EXISTS gr_operations (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL REFERENCES gr_sessions(id), data TEXT NOT NULL, global_generation INTEGER NOT NULL, feature_id TEXT REFERENCES gr_features(id), feature_generation INTEGER NOT NULL DEFAULT 0, cleanup_for TEXT REFERENCES gr_operations(id), cleanup_authorized INTEGER NOT NULL DEFAULT 0, cleanup_deadline REAL, stop_generation INTEGER, cancel_pending INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS gr_operation_session ON gr_operations(session_id,seq)",
            "CREATE TABLE IF NOT EXISTS gr_attempts (attempt_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES gr_operations(id), executor_id TEXT NOT NULL, executor_epoch INTEGER NOT NULL, dispatch_revision INTEGER, dispatched_at TEXT, receipt_hash TEXT, receipt TEXT)",
        )
        with self.store.connection(immediate=True) as db:
            for statement in statements:
                db.execute(statement)

    @staticmethod
    def _session(db, session_id):
        row = db.execute("SELECT * FROM gr_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise BrokerError("invalid_input", "Unknown game session.")
        return row, json.loads(row["data"])

    @staticmethod
    def _operation(db, operation_id):
        row = db.execute("SELECT * FROM gr_operations WHERE id=?", (operation_id,)).fetchone()
        if not row:
            raise BrokerError("invalid_input", "Unknown native operation.")
        return row, json.loads(row["data"])

    @staticmethod
    def _put_session(db, value):
        value = GameSession.model_validate(value).model_dump(mode="json")
        db.execute("UPDATE gr_sessions SET data=? WHERE id=?", (_json(value), value["id"]))
        return value

    @staticmethod
    def _put_operation(db, value, status=None):
        old = json.loads(db.execute("SELECT data FROM gr_operations WHERE id=?", (value["id"],)).fetchone()[0])
        if status is not None:
            if not can_transition(old["status"], status):
                raise BrokerError("revision_conflict", f"Invalid operation transition: {old['status']} -> {status}.")
            value["status"] = status
        value["revision"] = old["revision"] + 1
        value = NativeOperation.model_validate(value).model_dump(mode="json")
        changed = db.execute("UPDATE gr_operations SET data=? WHERE id=? AND json_extract(data,'$.revision')=?", (_json(value), value["id"], old["revision"])).rowcount
        if changed != 1:
            raise BrokerError("revision_conflict", "Operation revision changed.")
        return value

    @staticmethod
    def _event(db, event, value):
        db.execute("INSERT INTO events(type,data,created_at) VALUES(?,?,?)", (event, _json(value), now()))

    def create_session(self, target_id: str, harness_session_id: str, process_instance_id: str,
                       target_fingerprint: str | dict, knowledge_collection_ids=(), id: str | None = None):
        value = GameSession(id=id or new_id("game_session"), target_id=_nonempty(target_id, "target_id"),
                            harness_session_id=_nonempty(harness_session_id, "harness_session_id"),
                            process_instance_id=_nonempty(process_instance_id, "process_instance_id"),
                            session_generation=0, stop_generation=0, knowledge_collection_ids=list(knowledge_collection_ids),
                            state="running", last_error=None).model_dump(mode="json")
        fingerprint = _fingerprint(target_fingerprint)
        with self.store.connection(immediate=True) as db:
            if db.execute("SELECT 1 FROM gr_sessions WHERE id=?", (value["id"],)).fetchone():
                raise BrokerError("revision_conflict", "Session ID already exists.")
            generation = db.execute("SELECT value FROM gr_meta WHERE key='global_stop_generation'").fetchone()[0]
            db.execute("INSERT INTO gr_sessions(id,data,fingerprint,global_generation) VALUES(?,?,?,?)", (value["id"], _json(value), fingerprint, generation))
        return value

    def get_session(self, session_id: str):
        with self.store.connection() as db:
            return self._session(db, session_id)[1]

    def session_fingerprint(self, session_id: str) -> str:
        """Return the build-fingerprint bound to the session at creation.

        Exposed so :mod:`service` can author mapping recipes bound to
        the running target without duplicating the row lookup.
        """
        with self.store.connection() as db:
            row, _session = self._session(db, session_id)
            return row["fingerprint"]

    def session_context(self, session_id: str):
        with self.store.connection() as db:
            row, session = self._session(db, session_id)
            generation = db.execute("SELECT value FROM gr_meta WHERE key='global_stop_generation'").fetchone()[0]
            return {"session": session, "target_fingerprint": row["fingerprint"], "global_stop_generation": generation, "quiesced": bool(row["quiesced"])}

    def list_sessions(self):
        with self.store.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM gr_sessions ORDER BY rowid")]

    def get_operation(self, operation_id: str):
        with self.store.connection() as db:
            return self._operation(db, operation_id)[1]

    def operations(self, session_id: str | None = None):
        with self.store.connection() as db:
            rows = db.execute("SELECT data FROM gr_operations WHERE (? IS NULL OR session_id=?) ORDER BY seq", (session_id, session_id))
            return [json.loads(row[0]) for row in rows]

    def results(self, session_id: str | None = None):
        return [op for op in self.operations(session_id) if op["status"] in TERMINAL]

    @staticmethod
    def _feature(db, session_id, feature_id):
        # A feature name is scoped to its session, so two chats cannot silently
        # acquire the same writer merely by choosing identical names.
        key = f"{len(session_id)}:{session_id}:{feature_id}"
        row = db.execute("SELECT * FROM gr_features WHERE id=?", (key,)).fetchone()
        if not row:
            feature = FeatureRun(id=key, session_id=session_id, feature_id=feature_id, recipe_revisions=[], configuration={}, state="requested", readback_status="pending", behavior_status="pending", cleanup_status="not_applicable", evidence_ids=[]).model_dump(mode="json")
            db.execute("INSERT INTO gr_features(id,session_id,data) VALUES(?,?,?)", (key, session_id, _json(feature)))
            row = db.execute("SELECT * FROM gr_features WHERE id=?", (key,)).fetchone()
        return row

    def features(self, session_id=None):
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM gr_features WHERE (? IS NULL OR session_id=?) ORDER BY rowid", (session_id, session_id))]

    def restart_feature(self,session_id,feature_id):
        """Create a new generation only after the prior lifecycle cleaned up."""
        with self.store.connection(immediate=True) as db:
            _,session=self._session(db,session_id)
            if session['state']!='running':raise BrokerError('precondition_failed','Session is stopped.')
            row=self._feature(db,session_id,feature_id)
            value=json.loads(row['data'])
            if not row['stopped'] or value['state']!='stopped' or value['cleanup_status']!='pass':
                raise BrokerError('precondition_failed','Previous feature cleanup is not complete.')
            value.update(state='requested',readback_status='pending',behavior_status='pending',cleanup_status='not_applicable')
            db.execute('UPDATE gr_features SET data=?,generation=generation+1,stopped=0 WHERE id=?',(_json(value),row['id']))
            self._event(db,'game_runtime.feature_restarted',{'session_id':session_id,'feature_id':feature_id})

    def enqueue(self, session_id: str, kind: str, effect_class: str, arguments: dict,
                preconditions=(), feature_id: str | None = None, id: str | None = None):
        return self._enqueue(session_id, kind, effect_class, arguments, preconditions, feature_id, id)

    def declare_cleanup(self, parent_operation_id: str, *, kind: str, effect_class: str,
                        arguments: dict, preconditions, feature_id: str, id: str | None = None):
        """Private service-only declaration made before Stop, never model input.

        Preconditions are checked by the native service against the original
        recipe/process immediately before authorize_cleanup. The broker preserves
        the immutable declaration and permits one bounded attempt only.
        """
        parent = self.get_operation(parent_operation_id)
        if not preconditions:
            raise BrokerError("precondition_failed", "Cleanup requires explicit current-state preconditions.")
        return self._enqueue(parent["session_id"], kind, effect_class, arguments, preconditions, feature_id, id, parent_operation_id)

    def enqueue_with_cleanup(self, session_id, arguments, cleanup_arguments, feature_id):
        with self.store.connection(immediate=True) as db:
            parent = self._enqueue(session_id, 'write', 'mutating', arguments,
                ['same_process_birth', 'value_equals_read_receipt'], feature_id, None, db=db)
            self._enqueue(session_id, 'write', 'mutating', cleanup_arguments,
                ['same_process_birth', 'value_equals_written_value'], feature_id, None, parent['id'], db=db)
            return parent

    def cleanup_operations(self, session_id=None):
        with self.store.connection() as db:
            return [{'operation_id': r['id'], 'parent_id': r['cleanup_for']} for r in db.execute(
                'SELECT id,cleanup_for FROM gr_operations WHERE cleanup_for IS NOT NULL AND (? IS NULL OR session_id=?)', (session_id, session_id))]

    def _enqueue(self, session_id, kind, effect_class, arguments, preconditions, feature_id, id, cleanup_for=None, db=None):
        # Validate before claiming writer ownership, including JSON serializability.
        arguments = json.loads(_json(arguments))
        with (self.store.connection(immediate=True) if db is None else nullcontext(db)) as db:
            session_row, session = self._session(db, session_id)
            feature = self._feature(db, session_id, feature_id or session_id)
            request = dict(session_id=session_id, session_generation=session["session_generation"], stop_generation=session["stop_generation"], process_instance_id=session["process_instance_id"], target_fingerprint=session_row["fingerprint"], kind=kind, effect_class=effect_class, arguments=arguments, preconditions=list(preconditions))
            digest = hashlib.sha256(_json({**request, "feature_id": feature["id"], "cleanup_for": cleanup_for}).encode()).hexdigest()
            operation_id = id or new_id("native_op")
            old = db.execute("SELECT data FROM gr_operations WHERE id=?", (operation_id,)).fetchone()
            if old:
                value = json.loads(old[0])
                if value["request_hash"] == digest:
                    return value
                raise BrokerError("revision_conflict", "Operation ID is already bound to another request.")
            if session["state"] != "running" or feature["stopped"]:
                raise BrokerError("precondition_failed", "Stopped work cannot enqueue another operation.")
            if kind == "write" and effect_class == "read_only":
                raise BrokerError("invalid_input", "A write cannot be declared read-only.")
            if kind in {"read", "scan", "pointer_search"} and effect_class != "read_only":
                raise BrokerError("invalid_input", "Read operations must use the read_only effect class.")
            value = NativeOperation(id=operation_id, request_hash=digest, **request, status="queued", revision=0, executor_id=None, executor_epoch=None, lease_expires_at=None, attempt_id=None, result=None, error=None, evidence_ids=[]).model_dump(mode="json")
            if cleanup_for:
                source_row, source = self._operation(db, cleanup_for)
                if source_row["feature_id"] != feature["id"] or source["session_id"] != session_id or source["status"] not in {"queued", "claimed", "dispatched", "succeeded"}:
                    raise BrokerError("precondition_failed", "Cleanup must be predeclared for the same active feature.")
                if db.execute("SELECT 1 FROM gr_operations WHERE cleanup_for=?", (cleanup_for,)).fetchone():
                    raise BrokerError("revision_conflict", "This operation already has declared cleanup.")
                if source["effect_class"] == "read_only" or kind != "write" or effect_class == "read_only":
                    raise BrokerError("invalid_input", "Cleanup must bind a mutating source and a write operation.")
            if effect_class != "read_only":
                owner = db.execute("SELECT feature_id FROM gr_owners WHERE process_birth=?", (session["process_instance_id"],)).fetchone()
                if owner and owner[0] != feature["id"]:
                    raise BrokerError("busy", "Another feature owns mutation of this process instance.")
                db.execute("INSERT OR IGNORE INTO gr_owners VALUES(?,?)", (session["process_instance_id"], feature["id"]))
            db.execute("INSERT INTO gr_operations(id,session_id,data,global_generation,feature_id,feature_generation,cleanup_for) VALUES(?,?,?,?,?,?,?)", (value["id"], session_id, _json(value), session_row["global_generation"], feature["id"], feature["generation"], cleanup_for))
            return value

    @staticmethod
    def _executor_value(row):
        if row is None:
            return None
        return {"executor_id": row["id"], "executor_epoch": row["epoch"], "machine_id": row["machine_id"], "helper_process_instance_id": row["helper_birth"], "process_instance_id": row["process_birth"], "target_fingerprint": row["fingerprint"], "state": row["state"], "lease_expires_at": _iso(row["lease_until"])}

    def get_executor(self, executor_id):
        with self.store.connection() as db:
            return self._executor_value(db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone())

    def list_executors(self):
        with self.store.connection() as db:
            return [self._executor_value(r) for r in db.execute("SELECT * FROM gr_executors ORDER BY rowid")]

    def register_executor(self, executor_id: str, machine_id: str, helper_process_instance_id: str,
                          process_instance_id: str, target_fingerprint: str | dict, lease_seconds: float = 5):
        for name, value in (("executor_id", executor_id), ("machine_id", machine_id), ("helper_process_instance_id", helper_process_instance_id), ("process_instance_id", process_instance_id)):
            _nonempty(value, name)
        fingerprint, duration = _fingerprint(target_fingerprint), _seconds(lease_seconds)
        with self.store.connection(immediate=True) as db:
            old = db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone()
            if old and old["state"] != "dead":
                if old["machine_id"] == machine_id and old["helper_birth"] == helper_process_instance_id and old["process_birth"] == process_instance_id and old["fingerprint"] == fingerprint and old["state"] == "active" and old["lease_until"] > self.clock():
                    return self._executor_value(old)
                raise BrokerError("busy", "Existing helper death must be confirmed before executor replacement.")
            if db.execute("SELECT 1 FROM gr_executors WHERE machine_id=? AND process_birth=? AND state!='dead'", (machine_id, process_instance_id)).fetchone():
                raise BrokerError("busy", "A registered helper retains this process instance.")
            epoch = old["epoch"] + 1 if old else 0
            db.execute("INSERT INTO gr_executors VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET epoch=excluded.epoch,machine_id=excluded.machine_id,helper_birth=excluded.helper_birth,process_birth=excluded.process_birth,fingerprint=excluded.fingerprint,state=excluded.state,lease_until=excluded.lease_until,lease_seconds=excluded.lease_seconds,death_evidence=NULL", (executor_id, epoch, machine_id, helper_process_instance_id, process_instance_id, fingerprint, "active", self.clock() + duration, duration))
            return self._executor_value(db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone())

    def _executor(self, db, executor_id, executor_epoch, *, allow_suspect=False):
        row = db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone()
        if type(executor_epoch) is not int or not row or row["epoch"] != executor_epoch or row["state"] == "dead":
            raise BrokerError("executor_epoch_mismatch", "Executor epoch is no longer current.")
        if not allow_suspect and (row["state"] != "active" or row["lease_until"] <= self.clock()):
            raise BrokerError("target_unavailable", "Executor lease expired; helper recovery is required.")
        return row

    def _current(self, db, row, op, executor):
        session_row, session = self._session(db, op["session_id"])
        if op["process_instance_id"] != executor["process_birth"] or op["target_fingerprint"] != executor["fingerprint"]:
            raise BrokerError("precondition_failed", "Executor target identity does not match the operation.")
        if session["process_instance_id"] != op["process_instance_id"] or session_row["fingerprint"] != op["target_fingerprint"] or session["session_generation"] != op["session_generation"]:
            raise BrokerError("precondition_failed", "Operation belongs to an obsolete process or session generation.")
        feature = db.execute("SELECT * FROM gr_features WHERE id=?", (row["feature_id"],)).fetchone()
        if row["cleanup_for"]:
            if not row["cleanup_authorized"] or row["cleanup_deadline"] < self.clock() or session["stop_generation"] != row["stop_generation"]:
                raise BrokerError("precondition_failed", "Cleanup has not been authorized or its window expired.")
        elif session["state"] != "running" or session["stop_generation"] != op["stop_generation"] or feature["stopped"] or feature["generation"] != row["feature_generation"] or row["global_generation"] != db.execute("SELECT value FROM gr_meta WHERE key='global_stop_generation'").fetchone()[0]:
            raise BrokerError("precondition_failed", "Stop invalidated this operation.")
        if row["cancel_pending"]:
            raise BrokerError("precondition_failed", "Cancellation acknowledgement is pending.")
        if op["effect_class"] != "read_only":
            owner = db.execute("SELECT feature_id FROM gr_owners WHERE process_birth=?", (op["process_instance_id"],)).fetchone()
            if not owner or owner[0] != row["feature_id"]:
                raise BrokerError("busy", "Feature no longer owns this process's mutation lane.")

    def claim(self, executor_id: str, executor_epoch: int, session_id: str | None = None,
              operation_id: str | None = None):
        with self.store.connection(immediate=True) as db:
            executor = self._executor(db, executor_id, executor_epoch)
            # One operation at a time for every subscriber of a helper/process.
            if db.execute("SELECT 1 FROM gr_operations WHERE json_extract(data,'$.executor_id')=? AND json_extract(data,'$.executor_epoch')=? AND json_extract(data,'$.status') IN ('claimed','dispatched','cancellation_requested')", (executor_id, executor_epoch)).fetchone():
                return None
            rows = db.execute("SELECT * FROM gr_operations WHERE json_extract(data,'$.status')='queued' AND (? IS NULL OR session_id=?) AND (? IS NULL OR id=?) ORDER BY seq", (session_id, session_id, operation_id, operation_id)).fetchall()
            for row in rows:
                op = json.loads(row["data"])
                if op["process_instance_id"] != executor["process_birth"] or op["target_fingerprint"] != executor["fingerprint"]:
                    continue
                try:
                    self._current(db, row, op, executor)
                except BrokerError:
                    continue
                op.update(executor_id=executor_id, executor_epoch=executor_epoch, attempt_id=new_id("attempt"), lease_expires_at=_iso(executor["lease_until"]))
                op = self._put_operation(db, op, "claimed")
                db.execute("INSERT INTO gr_attempts(attempt_id,operation_id,executor_id,executor_epoch) VALUES(?,?,?,?)", (op["attempt_id"], op["id"], executor_id, executor_epoch))
                return op
        return None

    def _bound(self, db, operation_id, executor_id, executor_epoch, attempt_id, revision, bindings, *, allow_suspect=False):
        executor = self._executor(db, executor_id, executor_epoch, allow_suspect=allow_suspect)
        row, op = self._operation(db, operation_id)
        if op["executor_id"] != executor_id or op["executor_epoch"] != executor_epoch or op["attempt_id"] != attempt_id:
            raise BrokerError("executor_epoch_mismatch", "Operation attempt is bound to another executor.")
        if op["process_instance_id"] != executor["process_birth"] or op["target_fingerprint"] != executor["fingerprint"]:
            raise BrokerError("precondition_failed", "Operation target binding changed.")
        allowed = {"session_id", "session_generation", "stop_generation", "process_instance_id", "target_fingerprint", "request_hash"}
        if set(bindings) - allowed:
            raise BrokerError("invalid_input", "Unknown operation binding fields.")
        for key, value in bindings.items():
            if key == "target_fingerprint":
                value = _fingerprint(value)
            if type(value) is not type(op[key]) or value != op[key]:
                raise BrokerError("precondition_failed", f"Operation {key} binding is stale.")
        if type(revision) is not int or op["revision"] != revision:
            raise BrokerError("revision_conflict", "Operation revision changed.")
        return executor, row, op

    def dispatched(self, operation_id: str, executor_id: str, executor_epoch: int,
                   attempt_id: str, revision: int, **bindings):
        with self.store.connection(immediate=True) as db:
            executor, row, op = self._bound(db, operation_id, executor_id, executor_epoch, attempt_id, revision, bindings)
            if op["status"] != "claimed":
                raise BrokerError("revision_conflict", "Only claimed operations can record dispatch intent.")
            self._current(db, row, op, executor)
            op = self._put_operation(db, op, "dispatched")
            db.execute("UPDATE gr_attempts SET dispatch_revision=?,dispatched_at=? WHERE attempt_id=? AND dispatch_revision IS NULL", (op["revision"], now(), attempt_id))
            return op

    def complete(self, operation_id: str, executor_id: str, executor_epoch: int,
                 attempt_id: str, revision: int, status: str, result: dict | None = None,
                 error: dict | None = None, evidence_ids=(), accept_cancelled_attempt=False, **bindings):
        if status not in {"succeeded", "failed", "outcome_unknown"}:
            raise BrokerError("invalid_input", "Completion must have a terminal execution outcome.")
        if status == "succeeded" and error is not None:
            raise BrokerError("invalid_input", "Successful receipts cannot contain errors.")
        # Validate the receipt data before entering the commit/diagnostic path.
        receipt = dict(operation_id=operation_id, executor_id=executor_id, executor_epoch=executor_epoch, attempt_id=attempt_id, revision=revision, status=status, result=result, error=error, evidence_ids=list(evidence_ids), bindings=bindings)
        encoded = _json(receipt)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        conflict = None
        with self.store.connection(immediate=True) as db:
            self._executor(db, executor_id, executor_epoch, allow_suspect=True)
            attempt = db.execute("SELECT * FROM gr_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if attempt and attempt["receipt_hash"]:
                if attempt["receipt_hash"] == digest:
                    return self._operation(db, operation_id)[1]
                self._event(db, "game_runtime.conflicting_receipt", {"operation_id": operation_id, "attempt_id": attempt_id, "receipt_sha256": digest})
                conflict = BrokerError("revision_conflict", "A conflicting completion receipt was rejected.")
            else:
                if accept_cancelled_attempt and attempt and attempt['dispatch_revision'] is not None:
                    _, current=self._operation(db,operation_id)
                    if (current['status']=='cancellation_requested' and type(revision) is int
                        and attempt['dispatch_revision']<=revision<=current['revision']):
                        # The authenticated desktop returns the same already
                        # dispatched attempt after Stop advanced its CAS token.
                        # _bound still enforces executor, attempt and target.
                        revision=current['revision']
                _, row, op = self._bound(db, operation_id, executor_id, executor_epoch, attempt_id, revision, bindings, allow_suspect=True)
                if not attempt or attempt["dispatch_revision"] is None or op["status"] not in {"dispatched", "cancellation_requested"}:
                    raise BrokerError("revision_conflict", "Completion requires durable dispatch intent.")
                # A Stop receipt describes the old attempt's actual outcome; it
                # never restarts a feature or advances a later session generation.
                session_row, session = self._session(db, op["session_id"])
                if session["session_generation"] != op["session_generation"] or session["process_instance_id"] != op["process_instance_id"] or session_row["fingerprint"] != op["target_fingerprint"]:
                    raise BrokerError("precondition_failed", "Receipt belongs to an obsolete target generation.")
                op.update(result=result, error=error, evidence_ids=list(evidence_ids), lease_expires_at=None)
                op = self._put_operation(db, op, status)
                feature = db.execute('SELECT * FROM gr_features WHERE id=?', (row['feature_id'],)).fetchone()
                if not row['cleanup_for'] and not feature['stopped'] and op['kind'] == 'write':
                    value = json.loads(feature['data'])
                    next_state='monitoring' if op['arguments'].get('runtime_action') in ('feature_start','feature_tick') else 'verifying'
                    value.update(state=next_state if status == 'succeeded' else 'failed',
                                 readback_status='pass' if status == 'succeeded' and (result or {}).get('verified') else 'fail')
                    recipe=op['arguments'].get('recipe')
                    if recipe:
                        value['recipe_revisions']=[recipe['revision']]
                        value['configuration']={'recipe_id':recipe['id'],'apply_value':op['arguments'].get('value'),
                                                'disable_value':op['arguments'].get('disable_value')}
                    db.execute('UPDATE gr_features SET data=? WHERE id=?', (_json(value), feature['id']))
                db.execute("UPDATE gr_attempts SET receipt_hash=?,receipt=? WHERE attempt_id=? AND receipt_hash IS NULL", (digest, encoded, attempt_id))
                self._event(db, "game_runtime.completed", {"operation_id": operation_id, "attempt_id": attempt_id, "status": status})
                return op
        raise conflict

    def heartbeat(self, executor_id: str, executor_epoch: int):
        with self.store.connection(immediate=True) as db:
            executor = self._executor(db, executor_id, executor_epoch, allow_suspect=True)
            # A delayed heartbeat cannot turn an expired owner into an executor.
            if executor["state"] == "active" and executor["lease_until"] > self.clock():
                until = self.clock() + executor["lease_seconds"]
                db.execute("UPDATE gr_executors SET lease_until=? WHERE id=? AND epoch=?", (until, executor_id, executor_epoch))
                for row in db.execute("SELECT * FROM gr_operations WHERE json_extract(data,'$.executor_id')=? AND json_extract(data,'$.executor_epoch')=? AND json_extract(data,'$.status') IN ('claimed','dispatched','cancellation_requested')", (executor_id, executor_epoch)).fetchall():
                    op = json.loads(row["data"])
                    op["lease_expires_at"] = _iso(until)
                    # A lease extension is bookkeeping, not a new CAS revision.
                    db.execute("UPDATE gr_operations SET data=? WHERE id=?", (_json(op), op["id"]))
            else:
                db.execute("UPDATE gr_executors SET state='suspect' WHERE id=?", (executor_id,))
            value = self._executor_value(db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone())
            value["stop_requests"] = [json.loads(row["data"]) for row in db.execute("SELECT * FROM gr_operations WHERE json_extract(data,'$.executor_id')=? AND json_extract(data,'$.executor_epoch')=? AND (cancel_pending=1 OR json_extract(data,'$.status')='cancellation_requested')", (executor_id, executor_epoch))]
            return value

    def stop(self, session_id: str | None = None, feature_id: str | None = None):
        if feature_id is not None and session_id is None:
            raise BrokerError("invalid_input", "Feature Stop requires its session ID.")
        with self.store.connection(immediate=True) as db:
            if session_id is not None:
                self._session(db, session_id)
            if session_id is None:
                db.execute("UPDATE gr_meta SET value=value+1 WHERE key='global_stop_generation'")
            session_rows = db.execute("SELECT * FROM gr_sessions WHERE (? IS NULL OR id=?)", (session_id, session_id)).fetchall()
            stopped = []
            for session_row in session_rows:
                session = json.loads(session_row["data"])
                if feature_id is None:
                    if session["state"] not in {"stopping", "stopped", "interrupted"}:
                        session.update(stop_generation=session["stop_generation"] + 1, state="stopping")
                        self._put_session(db, session)
                        db.execute("UPDATE gr_sessions SET quiesced=0,stop_deadline=? WHERE id=?", (self.clock() + 2, session["id"]))
                features = db.execute("SELECT * FROM gr_features WHERE session_id=?", (session["id"],)).fetchall()
                keys = []
                for feature in features:
                    value = json.loads(feature["data"])
                    if feature_id is not None and value["feature_id"] != feature_id:
                        continue
                    if feature['stopped']:
                        continue
                    keys.append(feature["id"])
                    db.execute('UPDATE gr_sessions SET quiesced=0 WHERE id=?', (session['id'],))
                    value["state"] = "stopping"
                    db.execute("UPDATE gr_features SET stopped=1,generation=generation+1,data=? WHERE id=?", (_json(value), feature["id"]))
                for row in db.execute("SELECT * FROM gr_operations WHERE session_id=?", (session["id"],)).fetchall():
                    if row["feature_id"] not in keys:
                        continue
                    op = json.loads(row["data"])
                    if row["cleanup_for"] and op["status"] == "queued" and row["stop_generation"] is None:
                        db.execute("UPDATE gr_operations SET stop_generation=?,cleanup_deadline=? WHERE id=?", (session["stop_generation"], self.clock() + 2, op["id"]))
                    elif op["status"] == "queued":
                        self._put_operation(db, op, "cancelled_before_dispatch")
                    elif op["status"] == "claimed":
                        db.execute("UPDATE gr_operations SET cancel_pending=1 WHERE id=?", (op["id"],))
                        self._put_operation(db, op)
                    elif op["status"] == "dispatched":
                        self._put_operation(db, op, "cancellation_requested")
                stopped.append(session)
            self._event(db, "game_runtime.stop", {"session_id": session_id, "feature_id": feature_id})
            return stopped

    def ack_cancel(self, operation_id: str, executor_id: str, executor_epoch: int,
                   attempt_id: str, revision: int, **bindings):
        with self.store.connection(immediate=True) as db:
            _, row, op = self._bound(db, operation_id, executor_id, executor_epoch, attempt_id, revision, bindings, allow_suspect=True)
            if op["status"] != "claimed" or not row["cancel_pending"]:
                raise BrokerError("precondition_failed", "No undispatched cancellation is pending.")
            op = self._put_operation(db, op, "cancelled_before_dispatch")
            db.execute("UPDATE gr_operations SET cancel_pending=0 WHERE id=?", (operation_id,))
            return op

    def acknowledge_quiescence(self, session_id: str, executor_id: str, executor_epoch: int):
        with self.store.connection(immediate=True) as db:
            executor = self._executor(db, executor_id, executor_epoch, allow_suspect=True)
            session_row, session = self._session(db, session_id)
            if executor["process_birth"] != session["process_instance_id"] or executor["fingerprint"] != session_row["fingerprint"]:
                raise BrokerError("precondition_failed", "Quiescence belongs to another target.")
            if session["state"] != "stopping" and not db.execute("SELECT 1 FROM gr_features WHERE session_id=? AND stopped=1", (session_id,)).fetchone():
                raise BrokerError("precondition_failed", "No Stop is pending.")
            for row in db.execute("SELECT * FROM gr_operations WHERE session_id=? AND json_extract(data,'$.executor_id')=? AND json_extract(data,'$.executor_epoch')=?", (session_id, executor_id, executor_epoch)).fetchall():
                op = json.loads(row["data"])
                if row["cancel_pending"] and op["status"] == "claimed":
                    self._put_operation(db, op, "cancelled_before_dispatch")
                    db.execute("UPDATE gr_operations SET cancel_pending=0 WHERE id=?", (op["id"],))
                elif op["status"] == "cancellation_requested":
                    op["error"] = {"code": "outcome_unknown", "message": "Helper quiesced without a terminal execution receipt."}
                    self._put_operation(db, op, "outcome_unknown")
            db.execute("UPDATE gr_sessions SET quiesced=1 WHERE id=?", (session_id,))
            return session

    def authorize_cleanup(self, operation_id: str, executor_id: str, executor_epoch: int, *,
                          process_instance_id: str, target_fingerprint: str | dict,
                          preconditions_passed: bool):
        with self.store.connection(immediate=True) as db:
            executor = self._executor(db, executor_id, executor_epoch)
            row, op = self._operation(db, operation_id)
            session_row, session = self._session(db, op["session_id"])
            if not row["cleanup_for"] or op["status"] != "queued" or row["cleanup_authorized"] or row["stop_generation"] is None or row["cleanup_deadline"] < self.clock():
                raise BrokerError("precondition_failed", "No eligible predeclared cleanup exists in the quiescence window.")
            source_row, source = self._operation(db, row["cleanup_for"])
            feature = db.execute("SELECT * FROM gr_features WHERE id=?", (row["feature_id"],)).fetchone()
            if preconditions_passed is not True or not session_row["quiesced"] or source["status"] != "succeeded" or not feature["stopped"]:
                raise BrokerError("precondition_failed", "Cleanup requires successful original mutation, quiescence and observed current preconditions.")
            if (source["executor_id"], source["executor_epoch"]) != (executor_id, executor_epoch) or source_row["feature_id"] != row["feature_id"] or process_instance_id != op["process_instance_id"] or executor["process_birth"] != process_instance_id or _fingerprint(target_fingerprint) != op["target_fingerprint"] or executor["fingerprint"] != op["target_fingerprint"] or session["stop_generation"] != row["stop_generation"]:
                raise BrokerError("precondition_failed", "Cleanup process, feature or executor binding changed.")
            db.execute("UPDATE gr_operations SET cleanup_authorized=1,cleanup_deadline=? WHERE id=?", (self.clock() + 2, operation_id))
            return op

    def finish_stop(self, session_id: str, feature_id: str | None = None):
        with self.store.connection(immediate=True) as db:
            session_row, session = self._session(db, session_id)
            if not session_row["quiesced"]:
                raise BrokerError("precondition_failed", "Helper quiescence or confirmed helper death is required.")
            features = db.execute("SELECT * FROM gr_features WHERE session_id=? AND stopped=1", (session_id,)).fetchall()
            for feature in features:
                value = json.loads(feature["data"])
                if feature_id is not None and value["feature_id"] != feature_id:
                    continue
                rows = db.execute("SELECT * FROM gr_operations WHERE feature_id=?", (feature["id"],)).fetchall()
                if any(json.loads(r["data"])["status"] in ACTIVE for r in rows):
                    raise BrokerError("busy", "Native work is not yet terminal.")
                cleanup_status = "not_applicable"
                for row in rows:
                    op = json.loads(row["data"])
                    if row["cleanup_for"]:
                        cleanup_status = "pass" if op["status"] == "succeeded" else "fail"
                        if op["status"] == "queued":
                            op["error"] = {"code": "cleanup_timeout" if row["cleanup_authorized"] else "precondition_failed", "message": "Cleanup window closed without an observed completed cleanup."}
                            self._put_operation(db, op, "cancelled_before_dispatch")
                value.update(state="stopped", cleanup_status=cleanup_status)
                db.execute("UPDATE gr_features SET data=? WHERE id=?", (_json(value), feature["id"]))
                db.execute("DELETE FROM gr_owners WHERE feature_id=?", (feature["id"],))
            if feature_id is None and session["state"] == "stopping":
                session["state"] = "stopped"
                self._put_session(db, session)
            return session

    def acknowledge_stop(self, session_id: str, executor_id: str, executor_epoch: int):
        self.acknowledge_quiescence(session_id, executor_id, executor_epoch)
        return self.finish_stop(session_id)

    def recover(self, executor_id: str, executor_epoch: int, *, helper_death_confirmed: bool = False,
                death_evidence: dict | None = None):
        if type(helper_death_confirmed) is not bool:
            raise BrokerError("invalid_input", "helper_death_confirmed must be a boolean observed by the private service.")
        with self.store.connection(immediate=True) as db:
            executor = self._executor(db, executor_id, executor_epoch, allow_suspect=True)
            if not helper_death_confirmed and executor["lease_until"] > self.clock():
                raise BrokerError("busy", "A live lease cannot be reclaimed.")
            state = "dead" if helper_death_confirmed else "suspect"
            db.execute("UPDATE gr_executors SET state=?,death_evidence=? WHERE id=?", (state, _json(death_evidence or {}) if helper_death_confirmed else None, executor_id))
            affected = []
            for row in db.execute("SELECT * FROM gr_operations WHERE json_extract(data,'$.executor_id')=? AND json_extract(data,'$.executor_epoch')=?", (executor_id, executor_epoch)).fetchall():
                op = json.loads(row["data"])
                if op["status"] in {"dispatched", "cancellation_requested"}:
                    op["error"] = {"code": "outcome_unknown", "message": "Execution receipt was lost; this attempt will not be replayed."}
                    affected.append(self._put_operation(db, op, "outcome_unknown"))
                elif op["status"] == "claimed" and helper_death_confirmed:
                    op["error"] = {"code": "helper_exited", "message": "Helper death confirmed before dispatch."}
                    affected.append(self._put_operation(db, op, "cancelled_before_dispatch"))
            if helper_death_confirmed:
                # Every subscriber loses the same helper, including read-only
                # sessions that happened to have no operation in flight.
                for sr in db.execute("SELECT * FROM gr_sessions").fetchall():
                    session = json.loads(sr["data"])
                    if session["process_instance_id"] != executor["process_birth"] or session['state'] in {'stopped', 'interrupted'}:
                        continue
                    session.update(state="interrupted", stop_generation=session["stop_generation"] + 1, last_error="helper_exited")
                    self._put_session(db, session)
                    db.execute("UPDATE gr_sessions SET quiesced=1 WHERE id=?", (session["id"],))
                    for row in db.execute("SELECT * FROM gr_operations WHERE session_id=?", (session["id"],)).fetchall():
                        op = json.loads(row["data"])
                        if op["status"] == "queued":
                            affected.append(self._put_operation(db, op, "cancelled_before_dispatch"))
                    for feature in db.execute("SELECT * FROM gr_features WHERE session_id=?", (session["id"],)).fetchall():
                        value = json.loads(feature["data"])
                        value.update(state="failed", cleanup_status="fail" if value["cleanup_status"] == "pending" else value["cleanup_status"])
                        db.execute("UPDATE gr_features SET data=?,stopped=1,generation=generation+1 WHERE id=?", (_json(value), feature["id"]))
                        db.execute("DELETE FROM gr_owners WHERE feature_id=?", (feature["id"],))
            self._event(db, "game_runtime.recovery", {"executor_id": executor_id, "executor_epoch": executor_epoch, "helper_death_confirmed": helper_death_confirmed, "state": state})
            return {"executor": self._executor_value(db.execute("SELECT * FROM gr_executors WHERE id=?", (executor_id,)).fetchone()), "operations": affected, "replacement_allowed": helper_death_confirmed}


# A short alias is convenient for integrations without changing the wire names.
NativeBroker = GameRuntimeBroker
