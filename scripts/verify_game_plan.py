#!/usr/bin/env python3
"""P00 verifier CLI for game-runtime phase reports.

Validates reports against ``docs/game-lab/phase-manifest.json``:
report shape and required fields, check shape, reviewer vs owner,
exit_code vs result consistency, artifact hashes, evidence class
sufficiency, prerequisite gating and milestone status.

Exit codes:
  0  All required checks and prerequisite gates passed.
  1  Failed, invalid or stale evidence.
  2  Required checks are blocked or not_run.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SCREENSHOT_KINDS = {"screenshot", "model_note"}
_STATUS_ORDER = {"pass": 0, "not_run": 1, "blocked": 2, "fail": 3}
REPO_ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


PROGRESS_PHASE_FIELDS = ("implementation_status", "lane_results", "review_report", "review_notes")
PROGRESS_CHECK_FIELDS = ("result", "report_ref")


def source_snapshot_digest(refs):
    """Digest the declared source inventory as sorted ``path\\tsha256`` lines."""
    lines = sorted(
        f"{ref['path']}\t{ref['sha256'].lower()}"
        for ref in (refs or [])
        if isinstance(ref, dict)
        and ref.get("kind") == "source_snapshot"
        and _text(ref.get("path"))
        and isinstance(ref.get("sha256"), str)
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _phase_contract(phase):
    """Phase slice containing contract fields only, excluding progress bookkeeping."""
    entry = {k: v for k, v in phase.items() if k not in PROGRESS_PHASE_FIELDS}
    entry["checks"] = [
        {k: v for k, v in c.items() if k not in PROGRESS_CHECK_FIELDS}
        for c in phase.get("checks", [])
    ]
    return entry


def phase_manifest_contract_digest(phase_dict):
    """Canonical sha256 over the phase's declared contract fields.

    Progress fields (implementation_status, lane_results, review_report,
    review_notes, checks[].result, checks[].report_ref) are excluded so a
    status change does not invalidate a frozen manifest input.
    """
    if not isinstance(phase_dict, dict):
        return hashlib.sha256(b"").hexdigest()
    encoded = json.dumps(_phase_contract(phase_dict), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _manifest_contract(manifest):
    """Exclude progress bookkeeping when comparing an immutable input copy."""
    value = {key: manifest.get(key) for key in (
        "schema_version", "plan_id", "plan_revision", "contract_version",
        "status_policy", "report_required_fields", "check_report_required_fields", "milestones",
    )}
    value["phases"] = [_phase_contract(phase) for phase in manifest.get("phases", [])]
    return value


def _validate_refs(refs, base, label, errors, repo_root, *, allow_empty=False, run_root=None):
    if not isinstance(refs, list) or (not refs and not allow_empty):
        errors.append(f"FAIL {label} must contain hashed artifact references")
        return False
    ok = True
    seen = set()
    for ref in refs:
        if (not isinstance(ref, dict) or not _text(ref.get("path"))
                or not isinstance(ref.get("sha256"), str)
                or not _SHA256.fullmatch(ref["sha256"])):
            errors.append(f"FAIL {label} requires path and a 64-character sha256")
            ok = False
            continue
        scope = ref.get("scope")
        root = Path(repo_root if scope == "repository" else
                    (run_root or base) if scope == "run" else base).resolve()
        if scope not in (None, "repository", "run"):
            errors.append(f"FAIL {label} has unsupported artifact scope {scope!r}")
            ok = False
            continue
        try:
            path = (root / ref["path"]).resolve()
        except (OSError, ValueError) as exc:
            errors.append(f"FAIL {label} invalid artifact path: {exc}")
            ok = False
            continue
        if not path.is_relative_to(root) or not path.is_file():
            errors.append(f"FAIL {label} artifact '{path}' missing or outside its root")
            ok = False
            continue
        if path in seen:
            errors.append(f"FAIL {label} duplicate artifact '{path}'")
            ok = False
            continue
        seen.add(path)
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            errors.append(f"FAIL {label} cannot read '{path}': {exc}")
            ok = False
            continue
        if actual != ref["sha256"].lower():
            errors.append(f"FAIL {label} artifact '{path}' sha256 mismatch")
            ok = False
    return ok


def _validate_provenance(manifest, phase, report, run_dir, errors):
    repo_root = Path(manifest["_repo_root"])
    for field, expected in (("plan_id", manifest["plan_id"]),
                            ("plan_revision", manifest["plan_revision"]),
                            ("contract_version", manifest.get("contract_version", 1)),
                            ("phase_id", phase["id"]), ("run_id", run_dir.name)):
        if type(report.get(field)) is not type(expected) or report.get(field) != expected:
            errors.append(f"FAIL report {field} does not match {expected!r}")
    for field in ("environment", "target_identity"):
        if not isinstance(report.get(field), dict) or not report[field]:
            errors.append(f"FAIL report '{field}' must be a non-empty object")
    if not isinstance(report.get("blockers"), list):
        errors.append("FAIL report 'blockers' must be a list (empty is allowed)")
    for field in ("owner", "reviewer"):
        identity = report.get(field)
        if field == "reviewer" and identity is None:
            continue
        if not isinstance(identity, dict) or not all(_text(identity.get(k)) for k in ("id", "name")):
            errors.append(f"FAIL report '{field}' needs non-empty name and id")

    inputs = report.get("input_artifacts")
    artifacts = report.get("artifacts")
    inputs_ok = _validate_refs(inputs, repo_root, "input_artifacts", errors, repo_root, run_root=run_dir)
    refs_ok = _validate_refs(artifacts, repo_root, "artifacts", errors, repo_root, run_root=run_dir)
    snapshots = [r for r in (artifacts or []) if isinstance(r, dict) and r.get("kind") == "source_snapshot"] if isinstance(artifacts, list) else []
    manifest_inputs = [r for r in inputs if isinstance(r, dict) and r.get("kind") == "phase_manifest"] if isinstance(inputs, list) else []
    if not manifest_inputs:
        errors.append("FAIL input_artifacts needs a hashed phase_manifest input")
    elif inputs_ok:
        for ref in manifest_inputs:
            root = run_dir if ref.get("scope") == "run" else repo_root
            try:
                captured = json.loads((root / ref["path"]).read_text())
                if _manifest_contract(captured) != _manifest_contract(manifest):
                    errors.append("FAIL phase_manifest input contract differs from the current manifest")
            except (OSError, ValueError, TypeError, AttributeError):
                errors.append("FAIL phase_manifest input is not a valid manifest object")
    covered = {str(Path(r["path"])) for r in snapshots if _text(r.get("path"))}
    for relative in phase.get("source_paths", []):
        if not _text(relative):
            errors.append("FAIL source_paths entries must be repository-relative strings")
            continue
        path = (repo_root / relative).resolve()
        if not path.is_relative_to(repo_root) or not path.exists():
            errors.append(f"FAIL declared source path '{relative}' is missing or outside the repository")
            continue
        files = path.rglob("*") if path.is_dir() else [path]
        for source in files:
            if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            if str(source.relative_to(repo_root)) not in covered:
                errors.append(f"FAIL source_snapshot inventory omits '{source.relative_to(repo_root)}'")
    revision = report.get("implementation_revision")
    if not snapshots:
        errors.append("FAIL report needs source_snapshot artifacts binding the implementation")
    elif any(ref.get("scope") not in (None, "repository") for ref in snapshots):
        errors.append("FAIL source_snapshot artifacts must bind current repository paths")
    elif refs_ok:
        digest = "sha256:" + source_snapshot_digest(snapshots)
        if revision != digest:
            if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
                errors.append("FAIL implementation_revision must identify the source snapshot digest or exact Git commit")
            else:
                for ref in snapshots:
                    try:
                        result = subprocess.run(
                            ["git", "show", f"{revision}:{ref['path']}"], cwd=repo_root,
                            capture_output=True, timeout=10, check=False,
                        )
                        if result.returncode or hashlib.sha256(result.stdout).hexdigest() != ref["sha256"].lower():
                            errors.append(f"FAIL implementation_revision does not contain source snapshot '{ref['path']}'")
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        errors.append(f"FAIL cannot verify implementation_revision: {exc}")
                        break

    if report.get("reviewer") is not None:
        if report.get("reviewed_revision") != revision:
            errors.append("FAIL reviewed_revision differs from implementation_revision")
        replay = report.get("independent_replay")
        if not isinstance(replay, dict) or replay.get("performed") is not True:
            errors.append("FAIL independent_replay.performed must be true for acceptance")
        else:
            results = replay.get("reproduced_results")
            if not isinstance(results, dict):
                errors.append("FAIL independent_replay.reproduced_results must be an object")
            else:
                for check in report.get("checks", []):
                    if isinstance(check, dict) and check.get("result") == "pass" and (
                            not _text(check.get("id")) or results.get(check["id"]) != "pass"):
                        errors.append(f"FAIL independent_replay does not confirm '{check.get('id')}'")
            _validate_refs(replay.get("artifact_refs"), run_dir, "independent_replay", errors, repo_root)

    return not errors


def _load_manifest(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"FAIL unable to read manifest '{path}': {exc}"
    except json.JSONDecodeError as exc:
        return None, f"FAIL manifest '{path}' is not valid JSON: {exc}"
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        return None, f"FAIL manifest '{path}' schema_version must equal 1"
    def require(condition):
        if not condition:
            raise ValueError("invalid manifest")
    try:
        require(_text(data["plan_id"]) and type(data["plan_revision"]) is int)
        require(isinstance(data["phases"], list) and data["phases"])
        for name in ("report_required_fields", "check_report_required_fields"):
            require(isinstance(data[name], list) and data[name] and all(_text(v) for v in data[name]))
        phases = {p["id"]: p for p in data["phases"]}
        milestones = {m["id"]: m for m in data.get("milestones", [])}
        require(len(phases) == len(data["phases"]))
        require(len(milestones) == len(data.get("milestones", [])))
        all_checks = set()
        for pid, phase in phases.items():
            require(phase["checks"])
            require(isinstance(phase.get("source_paths", []), list))
            for check in phase["checks"]:
                cid = check["id"]
                require(cid.startswith(pid + ".") and cid not in all_checks)
                all_checks.add(cid)
        edges = {"p:" + pid: ["p:" + d for d in p.get("depends_on", [])]
                 + ["m:" + d for d in p.get("milestone_dependencies", [])] for pid, p in phases.items()}
        for mid, milestone in milestones.items():
            require(all(cid in all_checks for cid in milestone.get("required_checks", [])))
            edges["m:" + mid] = ["p:" + d for d in milestone.get("required_phases", [])]
            edges["m:" + mid] += ["p:" + cid.split(".")[0] for cid in milestone.get("required_checks", [])]
        visited, visiting = set(), set()
        def visit(node):
            require(node in edges and node not in visiting)
            if node in visited:
                return
            visiting.add(node)
            for child in edges[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)
        for node in edges:
            visit(node)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "FAIL malformed manifest, duplicate identifiers, unknown dependency or dependency cycle"
    return data, None


def _is_empty_required(value):
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    return False


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_latest_run(reports_dir, phase_id):
    phase_dir = Path(reports_dir) / phase_id
    if not phase_dir.is_dir():
        return None
    runs = sorted(
        p for p in phase_dir.iterdir()
        if p.is_dir() and (p / "report.json").is_file()
    )
    if not runs:
        return None
    return runs[-1]


def _peek_report_platform(run_dir):
    """Return target_identity.platform from the report, or None."""
    try:
        data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ident = data.get("target_identity")
    if isinstance(ident, dict):
        platform = ident.get("platform")
        if isinstance(platform, str) and platform:
            return platform
    return None


def _find_latest_runs_by_lane(reports_dir, phase):
    """Select the latest acceptable report per declared lane.

    For ``lanes == ["shared"]`` the latest alphabetical run wins.
    For multi-lane phases each run is bucketed by ``target_identity.platform``
    and the latest alphabetical run per lane wins. A run whose platform does
    not match any declared lane is preserved on disk but ignored here.
    """
    phase_dir = Path(reports_dir) / phase["id"]
    if not phase_dir.is_dir():
        return {}
    runs = sorted(
        p for p in phase_dir.iterdir()
        if p.is_dir() and (p / "report.json").is_file()
    )
    if not runs:
        return {}
    lanes = list(phase.get("lanes") or ["shared"])
    if lanes == ["shared"]:
        return {"shared": runs[-1]}
    selected = {}
    for run in runs:  # ascending alphabetical; later overwrites
        platform = _peek_report_platform(run)
        if platform in lanes:
            selected[platform] = run
    return selected


def _identity_matches(owner, reviewer):
    if not isinstance(owner, dict) or not isinstance(reviewer, dict):
        return False
    o_name = owner.get("name")
    o_id = owner.get("id")
    r_name = reviewer.get("name")
    r_id = reviewer.get("id")
    if o_name and r_name and o_name == r_name:
        return True
    if o_id and r_id and o_id == r_id:
        return True
    return False


def _validate_check_block(manifest, phase, check, run_dir, report_path, errors):
    """Return a normalized status string for a single check.

    Side effects: appends messages to ``errors``.
    """
    cid = check.get("id", "<unknown>") if isinstance(check, dict) else "<unknown>"
    required_fields = manifest["check_report_required_fields"]
    if not isinstance(check, dict):
        errors.append(f"FAIL check '{cid}' in '{report_path}' is not an object")
        return "fail"
    for field in required_fields:
        if field not in check:
            errors.append(
                f"FAIL check '{cid}' in '{report_path}' missing field '{field}'"
            )
            return "fail"

    result = check.get("result")
    exit_code = check.get("exit_code")
    for field in ("expected", "observed", "command", "working_directory"):
        if not _text(check.get(field)):
            errors.append(f"FAIL check '{cid}' has empty or invalid '{field}'")
            return "fail"
    if not isinstance(result, str) or result not in _STATUS_ORDER:
        errors.append(f"FAIL check '{cid}' has unknown result {result!r}")
        return "fail"
    if result == "pass" and (type(exit_code) is not int or exit_code != 0):
        errors.append(
            f"FAIL check '{cid}' in '{report_path}' has exit_code {exit_code} "
            f"but result 'pass'"
        )
        return "fail"

    # artifact refs
    artifact_refs = check.get("artifact_refs")
    if not _validate_refs(artifact_refs, run_dir, f"check '{cid}'", errors,
                          manifest["_repo_root"], allow_empty=result != "pass"):
        return "fail"

    # evidence class rules
    check_def = next(
        (c for c in phase["checks"] if c.get("id") == cid),
        {},
    )
    evidence_class = check_def.get("evidence_class")
    if evidence_class == "live_target_or_external_fixture" and result == "pass":
        has_live_evidence = any(
            (isinstance(ref, dict) and ref.get("kind") in {
                "log", "pytest_log", "native_receipt", "memory_capture", "telemetry",
            })
            for ref in artifact_refs
        )
        if not has_live_evidence:
            errors.append(
                f"FAIL check '{cid}' in '{report_path}' is "
                f"live_target_or_external_fixture but only has "
                f"screenshot/model_note evidence"
            )
            return "fail"

    if result == "pass":
        return "pass"
    if result == "fail":
        return "fail"
    if result == "blocked":
        policy = manifest.get("status_policy", {})
        absent_hw = policy.get("absent_hardware_result") == "blocked"
        limitations = check.get("limitations")
        if isinstance(limitations, list):
            limitations_text = " ".join(str(x) for x in limitations)
        else:
            limitations_text = str(limitations or "")
        if (
            evidence_class == "live_target_or_external_fixture"
            and absent_hw
            and "target_unavailable" in limitations_text
        ):
            return "blocked"
        return "blocked"
    return "not_run"


def _validate_report(manifest, phase, run_dir, errors):
    report_path = run_dir / "report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"FAIL unable to read '{report_path}': {exc}")
        return None, {}, True
    except json.JSONDecodeError as exc:
        errors.append(f"FAIL '{report_path}' is not valid JSON: {exc}")
        return None, {}, True

    if not isinstance(report, dict):
        errors.append(f"FAIL '{report_path}' must contain an object")
        return None, {}, True

    required_fields = manifest["report_required_fields"]
    for field in required_fields:
        if field not in report:
            errors.append(
                f"FAIL report '{report_path}' missing required field '{field}'"
            )
            return report, {}, True
        if field == "reviewer":
            # reviewer may be null (ready_for_review); handled below.
            continue
        if field in ("reviewed_revision", "independent_replay") and report.get("reviewer") is None:
            continue
        if _is_empty_required(report[field]):
            errors.append(
                f"FAIL report '{report_path}' has empty required field '{field}'"
            )
            return report, {}, True

    owner = report.get("owner") or {}
    reviewer = report.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        errors.append(
            f"FAIL report '{report_path}' reviewer must be an object or null"
        )
        return report, {}, True
    if reviewer and _identity_matches(owner, reviewer):
        errors.append(
            f"FAIL report '{report_path}' reviewer must differ from owner "
            f"(name or id collision)"
        )
        return report, {}, True

    checks_block = report.get("checks") or []
    if not isinstance(checks_block, list):
        errors.append(f"FAIL report '{report_path}' 'checks' must be a list")
        return report, {}, True

    _validate_provenance(manifest, phase, report, run_dir, errors)
    for definition in phase["checks"]:
        condition = definition.get("required_when")
        if condition and condition != "mobile_support_advertised_or_full_scope_acceptance":
            errors.append(f"FAIL unsupported conditional gate {condition!r}")
        elif condition and type(report.get("mobile_support_advertised")) is not bool:
            errors.append("FAIL report must declare boolean mobile_support_advertised for conditional gates")

    check_statuses = {}
    allowed_checks = {check["id"] for check in phase["checks"]}
    for check in checks_block:
        cid = check.get("id") if isinstance(check, dict) else None
        if not isinstance(cid, str) or cid not in allowed_checks or cid in check_statuses:
            errors.append(f"FAIL duplicate or unknown check '{cid}'")
            continue
        status = _validate_check_block(
            manifest, phase, check, run_dir, report_path, errors
        )
        if cid:
            check_statuses[cid] = status

    return report, check_statuses, bool(errors)


def _evaluate_phase_result(phase, check_statuses, strict, reviewer_present, errors, report=None):
    worst = "pass"
    for cdef in phase["checks"]:
        conditional = cdef.get("required_when") and (report or {}).get("mobile_support_advertised") is True
        if not cdef.get("required_for_phase", True) and not conditional:
            continue
        cid = cdef["id"]
        if cid not in check_statuses:
            errors.append(
                f"BLOCKED phase '{phase['id']}' required check '{cid}' missing "
                f"from report"
            )
            status = "not_run"
        else:
            status = check_statuses[cid]
        if strict and status == "blocked":
            status = "fail"
        if _STATUS_ORDER[status] > _STATUS_ORDER[worst]:
            worst = status
    if not reviewer_present and worst == "pass":
        return "ready_for_review"
    return worst


def _apply_prereqs(phase, phase_results, phase_reports, phase_errors, errors, phase_lanes=None):
    pid = phase["id"]
    depends_on = phase.get("depends_on") or []
    if not depends_on:
        return
    if phase_lanes is not None:
        for lane, state in phase_lanes.get(pid, {}).items():
            if state.get("report") is None:
                continue
            inputs = state["report"].get("input_artifacts") or []
            for dep in depends_on:
                candidates = phase_lanes.get(dep, {})
                upstream = candidates.get("shared") or candidates.get(lane)
                result = (upstream or {}).get("result", "not_run")
                problem = None
                if result != "pass":
                    problem = f"BLOCKED phase '{pid}' lane '{lane}' prerequisite '{dep}' result is '{result}'"
                    state["result"] = _downgrade(state["result"], "fail" if result == "fail" else "blocked")
                elif upstream.get("report") and upstream.get("run_dir"):
                    revision = upstream["report"].get("implementation_revision")
                    digest = _sha256_file(upstream["run_dir"] / "report.json")
                    if not any(isinstance(ref, dict) and ref.get("kind") == "phase_report"
                               and ref.get("phase_id") == dep
                               and ref.get("implementation_revision") == revision
                               and ref.get("sha256") == digest for ref in inputs):
                        problem = f"FAIL phase '{pid}' lane '{lane}' needs exact hashed phase_report input for prerequisite '{dep}'"
                        state["result"] = "fail"
                if problem:
                    state["evidence_eligible"] = False
                    errors.append(problem)
                    state["errors"].append(problem)
        actual = [s["result"] for s in phase_lanes.get(pid, {}).values() if s.get("report") is not None]
        if actual:
            phase_results[pid] = max(actual, key=lambda s: _STATUS_ORDER[s])
        return
    current_tuple = phase_reports.get(pid)
    input_artifacts = []
    if current_tuple and current_tuple[1]:
        input_artifacts = current_tuple[1].get("input_artifacts") or []

    for dep in depends_on:
        dep_result = phase_results.get(dep)
        if dep_result != "pass":
            errors.append(
                f"BLOCKED phase '{pid}' prerequisite '{dep}' result is "
                f"'{dep_result}'"
            )
            if phase_results.get(pid) == "pass":
                phase_results[pid] = "fail" if dep_result == "fail" else "blocked"
            elif phase_results.get(pid) in (None, "not_run"):
                phase_results[pid] = "blocked"
            continue
        dep_tuple = phase_reports.get(dep)
        if dep_tuple and dep_tuple[1]:
            dep_rev = dep_tuple[1].get("implementation_revision")
            dep_hash = _sha256_file(dep_tuple[0] / "report.json")
            if not any(isinstance(ref, dict) and ref.get("kind") == "phase_report"
                       and ref.get("phase_id") == dep
                       and ref.get("implementation_revision") == dep_rev
                       and ref.get("sha256") == dep_hash for ref in input_artifacts):
                errors.append(
                    f"FAIL phase '{pid}' needs exact hashed phase_report input "
                    f"for prerequisite '{dep}' at implementation_revision '{dep_rev}'"
                )
                if phase_results.get(pid) == "pass":
                    phase_results[pid] = "fail"


def _ensure_phase_evaluated(
    manifest, phases_by_id, reports_root, phase_id,
    phase_results, phase_reports, phase_errors, strict,
    phase_lanes=None, cli_overrides=None,
):
    if phase_id in phase_results:
        return
    if phase_lanes is None:
        phase_lanes = {}
    cli_overrides = cli_overrides or {}
    if phase_id not in phases_by_id:
        phase_results[phase_id] = "not_run"
        phase_reports[phase_id] = None
        phase_lanes[phase_id] = {}
        phase_errors[phase_id] = [
            f"FAIL unknown dependency phase '{phase_id}'"
        ]
        return
    phase = phases_by_id[phase_id]
    errors = []
    runs_by_lane = _find_latest_runs_by_lane(reports_root, phase)
    if not runs_by_lane:
        phase_results[phase_id] = "not_run"
        phase_reports[phase_id] = None
        phase_lanes[phase_id] = {}
        phase_errors[phase_id] = errors
        return

    lane_states = {}
    worst = "pass"
    primary_lane = None
    mobile_cli = cli_overrides.get("mobile_support_advertised")
    for lane in sorted(runs_by_lane):
        run_dir = runs_by_lane[lane]
        lane_errors = []
        report, check_statuses, fatal = _validate_report(
            manifest, phase, run_dir, lane_errors
        )
        effective_report = report
        if report is not None and mobile_cli is not None:
            effective_report = dict(report)
            effective_report["mobile_support_advertised"] = mobile_cli or report.get("mobile_support_advertised") is True
        if fatal:
            lane_result = "fail"
        else:
            reviewer_present = bool((report or {}).get("reviewer"))
            lane_result = _evaluate_phase_result(
                phase, check_statuses, strict, reviewer_present, lane_errors, effective_report
            )
            if lane_result == "ready_for_review":
                lane_errors.append(
                    f"BLOCKED phase '{phase_id}' lane '{lane}' reviewer missing: "
                    f"status ready_for_review, not verified"
                )
                lane_result = "blocked"
        lane_states[lane] = {
            "run_dir": run_dir,
            "report": report,
            "check_statuses": check_statuses,
            "evidence_eligible": not fatal and bool((report or {}).get("reviewer")),
            "result": lane_result,
            "errors": lane_errors,
        }
        for e in lane_errors:
            prefix = "" if lane == "shared" else f"[lane {lane}] "
            errors.append(f"{prefix}{e}")
        if _STATUS_ORDER[lane_result] > _STATUS_ORDER[worst]:
            worst = lane_result
        if primary_lane is None or _STATUS_ORDER[lane_result] > _STATUS_ORDER[lane_states[primary_lane]["result"]]:
            primary_lane = lane

    # Record declared multi-lanes that produced no selected run for milestone
    # math, but do not downgrade the phase result on missing lanes: completeness
    # is a milestone-level question (any_one_desktop_lane vs both-lane policies).
    declared_lanes = list(phase.get("lanes") or ["shared"])
    if declared_lanes != ["shared"]:
        for lane in declared_lanes:
            if lane not in lane_states:
                lane_states[lane] = {
                    "run_dir": None, "report": None, "check_statuses": {},
                    "result": "not_run", "errors": [],
                }
                errors.append(
                    f"WARN phase '{phase_id}' lane '{lane}' has no report"
                )

    phase_results[phase_id] = worst
    prim = lane_states[primary_lane] if primary_lane else None
    phase_reports[phase_id] = (prim["run_dir"], prim["report"], prim["check_statuses"]) if prim else None
    phase_lanes[phase_id] = lane_states
    phase_errors[phase_id] = errors

    # Recurse prereqs for the dep, so milestone math is honest.
    for dep in phase.get("depends_on") or []:
        _ensure_phase_evaluated(
            manifest, phases_by_id, reports_root, dep,
            phase_results, phase_reports, phase_errors, strict,
            phase_lanes, cli_overrides,
        )
    _apply_prereqs(
        phase, phase_results, phase_reports, phase_errors, errors, phase_lanes
    )
    milestones = {m["id"]: m for m in manifest.get("milestones", [])}
    for mid in phase.get("milestone_dependencies", []):
        def ensure(dep, _phase_lanes=phase_lanes, _cli=cli_overrides):
            _ensure_phase_evaluated(
                manifest, phases_by_id, reports_root, dep,
                phase_results, phase_reports, phase_errors, strict,
                _phase_lanes, _cli,
            )
        milestone_result = _evaluate_milestone(
            milestones[mid], phase_results, phase_reports, ensure,
            phase_lanes=phase_lanes, release_os=cli_overrides.get("release_os"),
        )
        if milestone_result != "pass":
            errors.append(
                f"BLOCKED phase '{phase_id}' milestone '{mid}' is '{milestone_result}'"
            )
            if phase_results[phase_id] == "pass":
                phase_results[phase_id] = "fail" if milestone_result == "fail" else "blocked"
            for state in phase_lanes.get(phase_id, {}).values():
                if state.get("report") is not None:
                    state["evidence_eligible"] = False
                    state["result"] = _downgrade(state["result"], "fail" if milestone_result == "fail" else "blocked")


DESKTOP_LANES = ("macos", "windows")


def _downgrade(current, new):
    if _STATUS_ORDER[new] > _STATUS_ORDER[current]:
        return new
    return current


def _target_key(identity):
    if not isinstance(identity, dict):
        return None
    platform = identity.get("platform")
    device = identity.get("device_id")
    fingerprint = identity.get("build_fingerprint")
    if not (platform and device and fingerprint is not None):
        return None
    return (platform, device, json.dumps(fingerprint, sort_keys=True))


def _lane_check_status(phase_lanes, phase_id, lane, check_id):
    lanes = (phase_lanes or {}).get(phase_id) or {}
    state = lanes.get(lane)
    if not state:
        return "not_run"
    if not state.get("evidence_eligible", state.get("result") == "pass"):
        return state.get("result", "not_run")
    return state.get("check_statuses", {}).get(check_id, "not_run")


def _any_one_desktop_lane(milestone, required_checks, phase_lanes):
    """Pass iff at least one desktop lane has all required_checks passing."""
    for lane in DESKTOP_LANES:
        if (all(_lane_check_status(phase_lanes, rc.split(".")[0], lane, rc) == "pass"
                for rc in required_checks)
                and all((phase_lanes.get(pid) or {}).get(lane, {}).get("result") == "pass"
                        for pid in milestone.get("required_phases", []))):
            return "pass"
    # If everything that is present passed but a lane is missing: blocked.
    saw_pass = any(_lane_check_status(phase_lanes, rc.split(".")[0], lane, rc) == "pass"
                   for lane in DESKTOP_LANES for rc in required_checks)
    return "blocked" if saw_pass else "not_run"


def _named_lane(milestone, required_checks, required_phases, phase_lanes, phase_results, lane):
    if lane not in DESKTOP_LANES:
        return "blocked"
    status = "pass"
    for rp in required_phases:
        lanes = (phase_lanes or {}).get(rp) or {}
        state = lanes.get(lane)
        if not state:
            return _downgrade(status, "not_run")
        status = _downgrade(status, state.get("result", "not_run"))
    for rc in required_checks:
        st = _lane_check_status(phase_lanes, rc.split(".")[0], lane, rc)
        status = _downgrade(status, st)
    return status


def _same_exact_target_policy(required_checks, phase_lanes, platform_filter=None, required_phases=()):
    """Pass iff a single (platform, device_id, build_fingerprint) carries all checks."""
    # Collect candidate targets across lanes keyed by identity tuple.
    candidates = {}
    for rc in required_checks:
        pid = rc.split(".")[0]
        lanes = (phase_lanes or {}).get(pid) or {}
        for lane, state in lanes.items():
            if not state.get("evidence_eligible", state.get("result") == "pass"):
                continue
            report = state.get("report") or {}
            key = _target_key(report.get("target_identity"))
            if key is None:
                continue
            if platform_filter and key[0] != platform_filter:
                continue
            if state.get("check_statuses", {}).get(rc) == "pass":
                candidates.setdefault(key, set()).add(rc)
    needed = set(required_checks)
    phase_targets = []
    for pid in required_phases:
        targets = {_target_key((state.get("report") or {}).get("target_identity"))
                   for state in (phase_lanes.get(pid) or {}).values()
                   if state.get("result") == "pass"}
        targets.discard(None)
        phase_targets.append(targets)
    if not required_checks and phase_targets:
        for key in set.intersection(*phase_targets):
            if not platform_filter or key[0] == platform_filter:
                candidates[key] = set()
    for key, passed in candidates.items():
        if passed >= needed and all(key in targets for targets in phase_targets):
            return "pass"
    return "blocked"


def _evaluate_milestone(milestone, phase_results, phase_reports, ensure_fn,
                         phase_lanes=None, release_os=None):
    required_phases = milestone.get("required_phases") or []
    required_checks = milestone.get("required_checks") or []
    policy = milestone.get("lane_policy")

    # Ensure each referenced phase is evaluated so downstream state is complete.
    for rp in required_phases:
        ensure_fn(rp)
    for rc in required_checks:
        ensure_fn(rc.split(".")[0])

    # When per-lane state is available and a lane_policy is declared, the
    # policy is authoritative. Aggregating worst-case phase_results would
    # downgrade correct policy outcomes with missing-lane entries.
    if phase_lanes and policy:
        if policy == "any_one_desktop_lane":
            if required_checks:
                return _any_one_desktop_lane(milestone, required_checks, phase_lanes)
            for lane in DESKTOP_LANES:
                if all((phase_lanes.get(rp) or {}).get(lane, {}).get("result") == "pass"
                       for rp in required_phases):
                    return "pass"
            return "blocked"
        if policy == "same_exact_target":
            return _same_exact_target_policy(required_checks, phase_lanes, required_phases=required_phases)
        if policy == "same_exact_android_target":
            return _same_exact_target_policy(required_checks, phase_lanes, platform_filter="android", required_phases=required_phases)
        if policy == "named_desktop_lane":
            if release_os is None:
                return "blocked"
            return _named_lane(milestone, required_checks, required_phases,
                                phase_lanes, phase_results, release_os)
        if policy == "P10_both_desktop_lanes_and_P09_qualified_android_lane":
            pol = "pass"
            for lane in DESKTOP_LANES:
                p10 = (phase_lanes.get("P10") or {}).get(lane)
                if not p10 or p10.get("result") != "pass":
                    pol = _downgrade(pol, "blocked")
            p09_lanes = (phase_lanes.get("P09") or {})
            android_ok = any(
                state.get("result") == "pass"
                and ((state.get("report") or {}).get("target_identity") or {}).get("platform") == "android"
                for state in p09_lanes.values()
            )
            if not android_ok:
                pol = _downgrade(pol, "blocked")
            for rc in required_checks:
                if rc == "P10.mobile_integration":
                    mob = all(_lane_check_status(phase_lanes, "P10", lane, rc) == "pass"
                              for lane in DESKTOP_LANES)
                    if not mob:
                        pol = _downgrade(pol, "blocked")
            return pol
        # Unknown policy: refuse to infer success.
        return "blocked"

    # No policy or no lane state: generic aggregation over phase_results.
    status = "pass"
    for rp in required_phases:
        status = _downgrade(status, phase_results.get(rp, "not_run"))
    for rc in required_checks:
        phase_id = rc.split(".")[0]
        tup = phase_reports.get(phase_id)
        if not tup:
            status = _downgrade(status, "not_run")
            continue
        if phase_results.get(phase_id) != "pass":
            status = _downgrade(status, phase_results.get(phase_id, "not_run"))
            continue
        _, _, check_statuses = tup
        st = check_statuses.get(rc, "not_run")
        status = _downgrade(status, st)

    # Legacy single-report path preserved for callers without phase_lanes.
    if status == "pass":
        involved = set(required_phases) | {c.split(".")[0] for c in required_checks}
        reports = [phase_reports[p][1] for p in involved if phase_reports.get(p)]
        if policy in ("any_one_desktop_lane", "named_desktop_lane"):
            lanes = {report.get("lane") for report in reports}
            if len(lanes) != 1 or not lanes <= {"macos", "windows"}:
                return "blocked"
        elif policy in ("same_exact_target", "same_exact_android_target"):
            identities = [r.get("target_identity", {}) for r in reports]
            fingerprints = [i.get("build_fingerprint") for i in identities]
            devices = [i.get("device_id") for i in identities]
            if not all(fingerprints) or not all(devices):
                return "blocked"
            if len({json.dumps(f, sort_keys=True) for f in fingerprints}) != 1 or len(set(devices)) != 1:
                return "blocked"
            if policy == "same_exact_android_target" and any(r.get("lane") != "android" for r in reports):
                return "blocked"
        elif policy:
            return "blocked"
    return status


def _format_human(phases_order, phase_results, phase_errors, milestones_output):
    lines = []
    for pid in phases_order:
        lines.append(f"Phase {pid}: {phase_results.get(pid, 'not_run')}")
        for e in phase_errors.get(pid, []):
            lines.append(f"  {e}")
    for m in milestones_output:
        lines.append(f"Milestone {m['id']}: {m['result']}")
    return "\n".join(lines)


def _format_json(phases_to_eval, phase_results, phase_reports, milestones_output, phase_lanes=None):
    phase_lanes = phase_lanes or {}
    out = {"phases": [], "milestones": milestones_output}
    for phase in phases_to_eval:
        pid = phase["id"]
        tup = phase_reports.get(pid)
        checks_out = []
        if tup:
            _, _, check_statuses = tup
            for cdef in phase["checks"]:
                cid = cdef["id"]
                checks_out.append({
                    "id": cid,
                    "result": check_statuses.get(cid, "not_run"),
                })
        lane_out = {}
        for lane, state in (phase_lanes.get(pid) or {}).items():
            run_dir = state.get("run_dir")
            report = state.get("report") or {}
            lane_out[lane] = {
                "result": state.get("result", "not_run"),
                "run_id": report.get("run_id") or (run_dir.name if run_dir else None),
                "report_ref": (str(run_dir / "report.json") if run_dir else None),
            }
        out["phases"].append({
            "id": pid,
            "result": phase_results.get(pid, "not_run"),
            "checks": checks_out,
            "lanes": lane_out,
        })
    return json.dumps(out, indent=2)


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify game runtime phase reports."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--reports", required=True)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--repo-root", default=str(REPO_ROOT),
                        help="root used for repository artifact references")
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="emit machine-readable summary",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="treat a blocked required check as a failure",
    )
    parser.add_argument(
        "--release-os", default=None, choices=("macos", "windows"),
        help="bind desktop_release and named_desktop_lane milestones to this lane",
    )
    parser.add_argument(
        "--mobile-support-advertised", dest="mobile_support_advertised",
        action="store_true", default=None,
        help="advertise mobile support, making conditional mobile checks required",
    )
    parser.add_argument(
        "--no-mobile-support-advertised", dest="mobile_support_advertised",
        action="store_false",
        help="explicitly deny mobile support advertising",
    )
    args = parser.parse_args(argv)

    manifest, err = _load_manifest(args.manifest)
    if err:
        print(err, file=sys.stderr)
        return 1
    manifest["_repo_root"] = str(Path(args.repo_root).resolve())

    phases_by_id = {p["id"]: p for p in manifest["phases"]}
    if args.phase:
        if args.phase not in phases_by_id:
            print(f"FAIL unknown phase '{args.phase}'", file=sys.stderr)
            return 1
        phases_to_eval = [phases_by_id[args.phase]]
    else:
        phases_to_eval = manifest["phases"]

    reports_root = Path(args.reports)

    phase_results = {}
    phase_reports = {}
    phase_errors = {}
    phase_lanes = {}
    cli_overrides = {
        "release_os": args.release_os,
        "mobile_support_advertised": args.mobile_support_advertised,
    }

    def ensure(pid):
        _ensure_phase_evaluated(
            manifest, phases_by_id, reports_root, pid,
            phase_results, phase_reports, phase_errors, args.strict,
            phase_lanes, cli_overrides,
        )

    for phase in phases_to_eval:
        ensure(phase["id"])

    milestones_output = []
    for milestone in manifest.get("milestones", []):
        result = _evaluate_milestone(
            milestone, phase_results, phase_reports, ensure,
            phase_lanes=phase_lanes, release_os=args.release_os,
        )
        milestones_output.append({"id": milestone["id"], "result": result})

    eval_ids = [p["id"] for p in phases_to_eval]
    eval_set = set(eval_ids)
    has_fail = False
    has_blocked_or_not_run = False
    for pid in eval_ids:
        r = phase_results.get(pid, "not_run")
        if r == "fail":
            has_fail = True
        elif r in ("blocked", "not_run"):
            if args.strict and r == "blocked":
                has_fail = True
            else:
                has_blocked_or_not_run = True
    for milestone, out in zip(manifest.get("milestones", []), milestones_output):
        relevant_phases = set(milestone.get("required_phases") or []) | {
            rc.split(".")[0] for rc in (milestone.get("required_checks") or [])
        }
        if not (relevant_phases & eval_set):
            continue
        r = out["result"]
        if r == "fail":
            has_fail = True
        elif r in ("blocked", "not_run"):
            if args.strict and r == "blocked":
                has_fail = True
            else:
                has_blocked_or_not_run = True

    if has_fail:
        exit_code = 1
    elif has_blocked_or_not_run:
        exit_code = 2
    else:
        exit_code = 0

    if args.json_output:
        print(_format_json(
            phases_to_eval, phase_results, phase_reports, milestones_output,
            phase_lanes=phase_lanes,
        ))
    else:
        print(_format_human(
            eval_ids, phase_results, phase_errors, milestones_output
        ))

    for pid in eval_ids:
        for e in phase_errors.get(pid, []):
            print(e, file=sys.stderr)

    return exit_code


def main():  # pragma: no cover - thin wrapper
    sys.exit(run())


if __name__ == "__main__":
    main()
