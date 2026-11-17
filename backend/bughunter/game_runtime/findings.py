"""Durable, build-bound findings created only from succeeded native receipts.

A finding is something the agent measured: a class record, a field record,
a name pool, a static root, an object-array entry, or a value behaviour
seen while watching. It starts as ``candidate``; a second succeeded receipt
from a different operation that reproduces it promotes it to ``observed``.
``restart_verified`` is reachable only through the mapping lifecycle after
promotion, never here.

``abs:`` addresses are bound to the process instance that produced them, so
the same address in a later process is a different finding.
"""
from __future__ import annotations

import hashlib
import json
import re

from .contracts import ADDRESS_PATTERN

KINDS = ("class_layout", "field", "static_root", "name_pool", "object_array", "value_behavior", "pointer_path")
VALUE_TYPES = {"u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64", "bytes"}
MAX_PER_RECEIPT = 16
MAX_EVIDENCE_BYTES = 8192
_ADDRESS = re.compile(ADDRESS_PATTERN)


def _check(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValueError("finding must be an object")
    kind, title, address = proposal.get("kind"), proposal.get("title"), proposal.get("address")
    value_type, evidence = proposal.get("value_type"), proposal.get("evidence") or {}
    if kind not in KINDS:
        raise ValueError(f"unknown finding kind {kind!r}")
    if not isinstance(title, str) or not 2 <= len(title) <= 200:
        raise ValueError("finding title must be 2..200 characters")
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address) or not address.startswith(("abs:", "mod:")):
        raise ValueError(f"finding address must be abs:0x... or mod:Name+0x..., got {address!r}")
    if value_type is not None and value_type not in VALUE_TYPES:
        raise ValueError(f"unsupported finding value_type {value_type!r}")
    if not isinstance(evidence, dict) or len(json.dumps(evidence, allow_nan=False)) > MAX_EVIDENCE_BYTES:
        raise ValueError("finding evidence must be a JSON object under 8 KiB")
    return {"kind": kind, "title": title, "address": address, "value_type": value_type, "evidence": evidence}


def finding_id(target_fingerprint: str, process_instance_id: str, proposal: dict) -> str:
    scope = process_instance_id if proposal["address"].startswith("abs:") else ""
    digest = hashlib.sha256("|".join((target_fingerprint, scope, proposal["kind"], proposal["address"],
                                      proposal["title"])).encode()).hexdigest()
    return "finding_" + digest[:24]


def record_from_receipt(store, operation: dict, *, engine: str | None = None) -> list[dict]:
    """Persist the receipt's proposed findings; invalid proposals are skipped and counted."""
    if operation.get("status") != "succeeded" or operation.get("kind") != "read":
        return []
    result = operation.get("result") or {}
    proposals = result.get("findings") or []
    if not isinstance(proposals, list):
        return []
    saved, rejected = [], 0
    for raw in proposals[:MAX_PER_RECEIPT]:
        try:
            proposal = _check(raw)
        except (ValueError, TypeError):
            rejected += 1
            continue
        ident = finding_id(operation["target_fingerprint"], operation["process_instance_id"], proposal)
        existing = store.get("game_finding", ident)
        if existing:
            operations = existing["operation_ids"]
            if operation["id"] not in operations:
                operations = (operations + [operation["id"]])[-20:]
            status = existing["status"]
            if status == "candidate" and len(set(operations)) >= 2:
                status = "observed"
            record = {**existing, "operation_ids": operations, "status": status, "evidence": proposal["evidence"],
                      "seen_count": existing["seen_count"] + (operation["id"] not in existing["operation_ids"])}
        else:
            record = {**proposal, "session_id": operation["session_id"],
                      "target_fingerprint": operation["target_fingerprint"],
                      "process_instance_id": operation["process_instance_id"],
                      "engine": engine, "status": "candidate", "seen_count": 1,
                      "operation_ids": [operation["id"]], "recipe_id": None}
        saved.append(store.put("game_finding", record, id=ident))
    if saved or rejected:
        store.event("game_runtime.finding", {"session_id": operation["session_id"], "operation_id": operation["id"],
                                             "finding_ids": [f["id"] for f in saved], "rejected": rejected})
    return saved


def list_for(store, *, target_fingerprint: str, session_id: str | None = None, process_instance_id: str | None = None) -> list[dict]:
    rows = store.list("game_finding", target_fingerprint=target_fingerprint)
    out = []
    for row in rows:
        if session_id is not None and row["session_id"] != session_id and row["address"].startswith("abs:"):
            continue
        if process_instance_id is not None and row["address"].startswith("abs:") and row["process_instance_id"] != process_instance_id:
            continue
        out.append(row)
    return out
