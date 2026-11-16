"""Tests for ``scripts/verify_game_plan.py`` (P00.report_validator).

The module is loaded via importlib because it lives outside the
``backend`` package root. Tests build a minimal manifest and reports
tree in a tmp_path, invoke the module's ``run`` function and assert on
exit codes and stderr messages.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify_game_plan.py"


@pytest.fixture(scope="module")
def verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_game_plan", VERIFIER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _run(verifier, argv):
    """Invoke the CLI entry point; return (exit_code, stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    manifest_path = Path(argv[argv.index("--manifest") + 1])
    with redirect_stdout(out), redirect_stderr(err):
        code = verifier.run([*argv, "--repo-root", str(manifest_path.parent)])
    return code, out.getvalue(), err.getvalue()


def _sha256(data):
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


REPORT_REQUIRED_FIELDS = [
    "plan_id",
    "plan_revision",
    "contract_version",
    "phase_id",
    "run_id",
    "owner",
    "implementation_revision",
    "input_artifacts",
    "environment",
    "target_identity",
    "checks",
    "artifacts",
    "cleanup_result",
    "blockers",
    "next_action",
    "reviewer",
    "reviewed_revision",
    "independent_replay",
]

CHECK_REQUIRED_FIELDS = [
    "id",
    "result",
    "expected",
    "observed",
    "command",
    "working_directory",
    "exit_code",
    "artifact_refs",
    "limitations",
]


def _base_manifest(extra_checks=None, extra_phases=None):
    base_check = {
        "id": "P00.contracts",
        "evidence_class": "automated_or_reviewed_artifact",
        "required_for_phase": True,
        "result": "not_run",
        "report_ref": None,
    }
    checks = [base_check]
    if extra_checks:
        checks.extend(extra_checks)
    phases = [
        {
            "id": "P00",
            "title": "Contracts",
            "owner_role": "C",
            "reviewer_role": "V",
            "implementation_status": "not_started",
            "depends_on": [],
            "milestone_dependencies": [],
            "lanes": ["shared"],
            "expected_outcome": "ok",
            "required_outputs": [],
            "checks": checks,
        }
    ]
    if extra_phases:
        phases.extend(extra_phases)
    return {
        "schema_version": 1,
        "plan_id": "game-runtime-v1",
        "plan_revision": 1,
        "evidence_root": "<unused>",
        "status_policy": {
            "absent_hardware_result": "blocked",
            "reviewer_must_differ_from_author": True,
        },
        "milestones": [],
        "phases": phases,
        "report_required_fields": REPORT_REQUIRED_FIELDS,
        "check_report_required_fields": CHECK_REQUIRED_FIELDS,
    }


def _write_manifest(tmp_path, manifest):
    manifest_path = tmp_path / "phase-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


def _write_artifact(run_dir, name, data=b"ok\n"):
    artifact_path = run_dir / name
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(data)
    return {"path": name, "sha256": _sha256(data), "kind": "log"}


def _base_check(check_id="P00.contracts", result="pass", exit_code=0,
                artifact_refs=None, limitations="none"):
    return {
        "id": check_id,
        "result": result,
        "expected": "contracts valid",
        "observed": "contracts valid",
        "command": "pytest",
        "working_directory": "backend",
        "exit_code": exit_code,
        "artifact_refs": artifact_refs or [],
        "limitations": limitations,
    }


def _base_report(run_dir, check):
    root = run_dir.parents[2]
    source = root / "source.py"
    source.write_text("# test source snapshot\n")
    source_ref = {"path": "source.py", "sha256": _sha256(source.read_bytes()), "kind": "source_snapshot"}
    manifest_ref = {"path": "phase-manifest.json", "sha256": _sha256((root / "phase-manifest.json").read_bytes()), "kind": "phase_manifest"}
    lines = sorted([f"{source_ref['path']}\t{source_ref['sha256'].lower()}"])
    revision = "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()
    replay_ref = _write_artifact(run_dir, "reviewer-replay.log", b"Independent fixture replay\n")
    return {
        "plan_id": "game-runtime-v1",
        "plan_revision": 1,
        "contract_version": 1,
        "phase_id": "P00",
        "run_id": run_dir.name,
        "owner": {"name": "Alice", "id": "alice"},
        "implementation_revision": revision,
        "input_artifacts": [source_ref, manifest_ref],
        "environment": {"os": "macos"},
        "target_identity": {"id": "fixture"},
        "checks": [check],
        "artifacts": [source_ref],
        "cleanup_result": "clean",
        "blockers": [],
        "next_action": "proceed",
        "reviewer": {"name": "Bob", "id": "bob"},
        "reviewed_revision": revision,
        "independent_replay": {
            "performed": True,
            "reproduced_results": {check["id"]: check["result"]},
            "artifact_refs": [replay_ref],
        },
    }


def _write_passing_phase(tmp_path, manifest=None):
    """Create a passing P00 fixture under ``tmp_path``. Returns (manifest_path, reports_dir)."""
    if manifest is None:
        manifest = _base_manifest()
    manifest_path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / "reports"
    run_dir = reports / "P00" / "run-001"
    run_dir.mkdir(parents=True)
    artifact_ref = _write_artifact(run_dir, "evidence.log")
    check = _base_check(artifact_refs=[artifact_ref])
    (run_dir / "report.json").write_text(json.dumps(_base_report(run_dir, check)))
    return manifest_path, reports


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_passing_phase_exits_zero(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    code, stdout, _ = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 0, stdout
    assert "Phase P00: pass" in stdout


def test_missing_required_field_exits_one_and_quotes_field(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    report_file = reports / "P00" / "run-001" / "report.json"
    report = json.loads(report_file.read_text())
    report.pop("next_action")
    report_file.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "'next_action'" in stderr
    assert "FAIL" in stderr


def test_sha256_mismatch_exits_one_and_quotes_path(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    run_dir = reports / "P00" / "run-001"
    report_file = run_dir / "report.json"
    report = json.loads(report_file.read_text())
    # Corrupt the recorded sha256 instead of mutating the file.
    report["checks"][0]["artifact_refs"][0]["sha256"] = "0" * 64
    report_file.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "evidence.log" in stderr
    assert "sha256 mismatch" in stderr


def test_missing_prerequisite_phase_report_exits_two(tmp_path, verifier):
    manifest = _base_manifest(
        extra_phases=[
            {
                "id": "P01",
                "title": "Knowledge",
                "owner_role": "K",
                "reviewer_role": "V",
                "depends_on": ["P00"],
                "lanes": ["shared"],
                "expected_outcome": "ok",
                "required_outputs": [],
                "checks": [
                    {
                        "id": "P01.retrieval",
                        "evidence_class": "automated_or_reviewed_artifact",
                        "required_for_phase": True,
                        "result": "not_run",
                        "report_ref": None,
                    }
                ],
            }
        ]
    )
    manifest_path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / "reports"
    # Write a P01 report but no P00 report.
    run_dir = reports / "P01" / "run-001"
    run_dir.mkdir(parents=True)
    artifact_ref = _write_artifact(run_dir, "retrieval.log")
    check = _base_check(check_id="P01.retrieval", artifact_refs=[artifact_ref])
    report = _base_report(run_dir, check)
    report["phase_id"] = "P01"
    (run_dir / "report.json").write_text(json.dumps(report))

    code, _, stderr = _run(
        verifier,
        [
            "--manifest", str(manifest_path),
            "--reports", str(reports),
            "--phase", "P01",
        ],
    )
    assert code == 2
    assert "P00" in stderr
    assert "BLOCKED" in stderr


def test_screenshot_only_live_target_check_fails(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["checks"][0]["evidence_class"] = (
        "live_target_or_external_fixture"
    )
    manifest_path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / "reports"
    run_dir = reports / "P00" / "run-001"
    run_dir.mkdir(parents=True)
    shot = _write_artifact(run_dir, "shot.png", data=b"png\n")
    shot["kind"] = "screenshot"
    note = _write_artifact(run_dir, "model.txt", data=b"note\n")
    note["kind"] = "model_note"
    check = _base_check(artifact_refs=[shot, note])
    (run_dir / "report.json").write_text(json.dumps(_base_report(run_dir, check)))

    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "live_target_or_external_fixture" in stderr
    assert "screenshot/model_note" in stderr


def test_reviewer_equals_owner_fails(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    report_file = reports / "P00" / "run-001" / "report.json"
    report = json.loads(report_file.read_text())
    report["reviewer"] = {"name": "Alice", "id": "alice"}
    report_file.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "reviewer must differ from owner" in stderr


def test_exit_code_nonzero_but_pass_result_fails(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    report_file = reports / "P00" / "run-001" / "report.json"
    report = json.loads(report_file.read_text())
    report["checks"][0]["exit_code"] = 1
    report_file.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "exit_code 1" in stderr
    assert "'pass'" in stderr


def test_strict_upgrades_blocked_required_check_to_exit_one(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["checks"][0]["evidence_class"] = (
        "live_target_or_external_fixture"
    )
    manifest_path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / "reports"
    run_dir = reports / "P00" / "run-001"
    run_dir.mkdir(parents=True)
    artifact_ref = _write_artifact(run_dir, "blocked.log")
    check = _base_check(
        result="blocked",
        exit_code=0,
        artifact_refs=[artifact_ref],
        limitations="target_unavailable: no device",
    )
    (run_dir / "report.json").write_text(
        json.dumps(_base_report(run_dir, check))
    )

    # Without strict: exit 2 (blocked).
    code, _, _ = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 2

    # With strict: exit 1.
    code, _, _ = _run(
        verifier,
        [
            "--manifest", str(manifest_path),
            "--reports", str(reports),
            "--strict",
        ],
    )
    assert code == 1


def test_empty_reports_directory_for_phase_exits_two(tmp_path, verifier):
    manifest_path = _write_manifest(tmp_path, _base_manifest())
    reports = tmp_path / "reports"
    reports.mkdir()
    code, stdout, _ = _run(
        verifier,
        [
            "--manifest", str(manifest_path),
            "--reports", str(reports),
            "--phase", "P00",
        ],
    )
    assert code == 2
    assert "Phase P00: not_run" in stdout


def test_latest_run_is_picked_alphabetically(tmp_path, verifier):
    manifest_path, reports = _write_passing_phase(tmp_path)
    # Create a newer (alphabetically later) run that is broken; it should
    # be picked over run-001 and the verifier should fail.
    run_dir = reports / "P00" / "run-002"
    run_dir.mkdir(parents=True)
    artifact_ref = _write_artifact(run_dir, "evidence.log")
    check = _base_check(artifact_refs=[artifact_ref])
    report = _base_report(run_dir, check)
    report["run_id"] = "run-002"
    report.pop("blockers")
    (run_dir / "report.json").write_text(json.dumps(report))

    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "'blockers'" in stderr


@pytest.mark.parametrize("field,value", [
    ("plan_id", "unrelated-plan"), ("plan_revision", 99),
    ("contract_version", 99), ("phase_id", "P99"), ("run_id", "another-run"),
    ("implementation_revision", "not-the-tested-source"),
    ("reviewed_revision", "not-the-tested-source"),
    ("owner", {}), ("reviewer", {"role": "V"}),
])
def test_report_identity_and_revision_cannot_be_substituted(tmp_path, verifier, field, value):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    code, _, _ = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1


@pytest.mark.parametrize("collection", ["input_artifacts", "artifacts"])
def test_missing_or_changed_source_evidence_rejected(tmp_path, verifier, collection):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    report[collection][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1 and "sha256 mismatch" in stderr


def test_changed_implementation_invalidates_old_report(tmp_path, verifier):
    manifest, reports = _write_passing_phase(tmp_path)
    (tmp_path / "source.py").write_text("# changed after verification\n")
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1 and "sha256 mismatch" in stderr


@pytest.mark.parametrize("field,value", [
    ("expected", ""), ("observed", " "), ("command", ""),
    ("working_directory", None), ("artifact_refs", []),
    ("exit_code", "failed"), ("exit_code", None), ("exit_code", False),
    ("result", "verified"),
])
def test_empty_or_mistyped_check_cannot_pass(tmp_path, verifier, field, value):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    report["checks"][0][field] = value
    path.write_text(json.dumps(report))
    code, _, _ = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1


@pytest.mark.parametrize("performed,result", [(False, "pass"), (True, "blocked")])
def test_independent_review_must_confirm_pass(tmp_path, verifier, performed, result):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    replay = report["independent_replay"]
    replay["performed"] = performed
    replay["reproduced_results"]["P00.contracts"] = result
    path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1 and "independent_replay" in stderr


def test_missing_reviewer_is_pending_not_verified(tmp_path, verifier):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    report.update(reviewer=None, reviewed_revision=None, independent_replay=None)
    path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 2 and "reviewer missing" in stderr


def test_valid_duplicate_cannot_hide_a_bad_check(tmp_path, verifier):
    manifest, reports = _write_passing_phase(tmp_path)
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    bad = json.loads(json.dumps(report["checks"][0]))
    bad["artifact_refs"][0]["sha256"] = "0" * 64
    report["checks"].insert(0, bad)
    path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1 and "duplicate" in stderr


def test_explicit_milestone_is_a_prerequisite(tmp_path, verifier):
    manifest = _base_manifest(extra_phases=[{
        "id": "P01", "depends_on": [], "checks": [{"id": "P01.retrieval"}],
    }])
    manifest["milestones"] = [{"id": "knowledge_ready", "required_checks": ["P01.retrieval"]}]
    manifest["phases"][0]["milestone_dependencies"] = ["knowledge_ready"]
    path, reports = _write_passing_phase(tmp_path, manifest)
    code, _, stderr = _run(verifier, ["--manifest", str(path), "--reports", str(reports), "--phase", "P00"])
    assert code == 2 and "knowledge_ready" in stderr


def test_dependency_cycle_rejected_without_recursion(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["depends_on"] = ["P00"]
    path, reports = _write_passing_phase(tmp_path, manifest)
    code, _, stderr = _run(verifier, ["--manifest", str(path), "--reports", str(reports)])
    assert code == 1 and "cycle" in stderr


def test_evidence_cannot_escape_run_directory(tmp_path, verifier):
    manifest, reports = _write_passing_phase(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"outside evidence")
    path = reports / "P00/run-001/report.json"
    report = json.loads(path.read_text())
    report["checks"][0]["artifact_refs"] = [{"path": "../../../outside.log", "sha256": _sha256(outside.read_bytes()), "kind": "log"}]
    path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ["--manifest", str(manifest), "--reports", str(reports)])
    assert code == 1 and "outside its root" in stderr


def test_declared_implementation_file_cannot_be_omitted(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["source_paths"] = ["source.py", "omitted.py"]
    (tmp_path / "omitted.py").write_text("# an unrecorded implementation file\n")
    path, reports = _write_passing_phase(tmp_path, manifest)
    code, _, stderr = _run(verifier, ["--manifest", str(path), "--reports", str(reports)])
    assert code == 1 and "inventory omits 'omitted.py'" in stderr


def test_changed_contract_cannot_reuse_a_frozen_old_manifest(tmp_path, verifier):
    path, reports = _write_passing_phase(tmp_path)
    old = tmp_path / "old-manifest.json"
    old.write_bytes(path.read_bytes())
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    report["input_artifacts"][1]["path"] = old.name
    report_path.write_text(json.dumps(report))
    manifest = json.loads(path.read_text())
    manifest["phases"][0]["checks"][0]["evidence_class"] = "live_target_or_external_fixture"
    path.write_text(json.dumps(manifest))
    code, _, stderr = _run(verifier, ["--manifest", str(path), "--reports", str(reports)])
    assert code == 1 and "input contract differs" in stderr


def test_progress_updates_preserve_a_frozen_manifest_contract(tmp_path, verifier):
    path, reports = _write_passing_phase(tmp_path)
    old = tmp_path / "old-manifest.json"
    old.write_bytes(path.read_bytes())
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    report["input_artifacts"][1]["path"] = old.name
    report_path.write_text(json.dumps(report))
    manifest = json.loads(path.read_text())
    manifest["phases"][0]["implementation_status"] = "ready_for_review"
    manifest["phases"][0]["checks"][0]["result"] = "pass"
    path.write_text(json.dumps(manifest))
    code, _, stderr = _run(verifier, ["--manifest", str(path), "--reports", str(reports)])
    assert code == 0, stderr


def test_one_desktop_lane_cannot_certify_both_platforms(verifier):
    milestone = {"required_phases": ["P10", "P09"],
                 "lane_policy": "P10_both_desktop_lanes_and_P09_qualified_android_lane"}
    reports = {"P10": (None, {"lane": "macos"}, {}),
               "P09": (None, {"lane": "android"}, {})}
    assert verifier._evaluate_milestone(milestone, {"P10": "pass", "P09": "pass"},
                                       reports, lambda _: None) == "blocked"


def test_target_milestone_cannot_mix_device_builds(verifier):
    milestone = {"required_phases": ["P02", "P08"], "lane_policy": "same_exact_android_target"}
    reports = {pid: (None, {"lane": "android", "target_identity": {
        "device_id": "phone", "build_fingerprint": version}}, {})
        for pid, version in [("P02", "build-a"), ("P08", "build-b")]}
    assert verifier._evaluate_milestone(milestone, {"P02": "pass", "P08": "pass"},
                                       reports, lambda _: None) == "blocked"


@pytest.mark.parametrize("advertised,expected", [(False, 0), (True, 2), ("false", 1)])
def test_advertised_mobile_support_requires_conditional_check(tmp_path, verifier, advertised, expected):
    manifest = _base_manifest(extra_checks=[{
        "id": "P00.mobile_integration", "required_for_phase": False,
        "required_when": "mobile_support_advertised_or_full_scope_acceptance",
    }])
    path, reports = _write_passing_phase(tmp_path, manifest)
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    report["mobile_support_advertised"] = advertised
    report_path.write_text(json.dumps(report))
    code, _, _ = _run(verifier, ["--manifest", str(path), "--reports", str(reports)])
    assert code == expected


# ---------------------------------------------------------------------------
# Lane-aware report selection and milestone aggregation (phase P00 remediation)
# ---------------------------------------------------------------------------


def _desktop_phase_dict(phase_id, checks_ids, extra=None):
    """Build a multi-lane desktop phase declaration."""
    checks = [{
        "id": cid, "evidence_class": "automated_or_reviewed_artifact",
        "required_for_phase": True, "result": "not_run", "report_ref": None,
    } for cid in checks_ids]
    phase = {
        "id": phase_id, "title": phase_id, "owner_role": "T", "reviewer_role": "V",
        "implementation_status": "not_started", "depends_on": [],
        "milestone_dependencies": [], "lanes": ["macos", "windows"],
        "expected_outcome": "ok", "required_outputs": [], "checks": checks,
    }
    if extra:
        phase.update(extra)
    return phase


def _write_lane_run(reports_root, phase_id, run_name, platform, check_ids,
                    manifest_root, *, check_overrides=None,
                    device_id=None, build_fingerprint=None):
    """Create a report.json under reports_root/<phase_id>/<run_name>/ for a lane."""
    run_dir = reports_root / phase_id / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    for cid in check_ids:
        ref = _write_artifact(run_dir, f"{cid}.log", data=f"{cid}\n".encode())
        chk = _base_check(check_id=cid, artifact_refs=[ref])
        if check_overrides and cid in check_overrides:
            chk.update(check_overrides[cid])
        checks.append(chk)
    primary_check = checks[0]
    report = _base_report(run_dir, primary_check)
    report["phase_id"] = phase_id
    report["run_id"] = run_name
    report["checks"] = checks
    identity = {"id": "fixture", "platform": platform}
    if device_id:
        identity["device_id"] = device_id
    if build_fingerprint is not None:
        identity["build_fingerprint"] = build_fingerprint
    report["target_identity"] = identity
    report["independent_replay"]["reproduced_results"] = {
        c["id"]: c["result"] for c in checks
    }
    (run_dir / "report.json").write_text(json.dumps(report))
    return run_dir, report


def _multi_lane_fixture(tmp_path, phase_id, check_ids, platforms,
                        extra_phase=None, milestones=None, phase_source_py=False,
                        extra_phases=None):
    """Write a manifest and per-lane reports; return (manifest_path, reports_dir)."""
    desktop_phase = _desktop_phase_dict(phase_id, check_ids, extra=extra_phase or {})
    base = _base_manifest(extra_phases=(extra_phases or []) + [desktop_phase])
    # Drop the default P00 shared phase for cleaner multi-lane scenarios.
    base["phases"] = [p for p in base["phases"] if p["id"] != "P00" or phase_id == "P00"]
    if milestones:
        base["milestones"] = milestones
    manifest_path = _write_manifest(tmp_path, base)
    reports = tmp_path / "reports"
    (tmp_path / "source.py").write_text("# test source snapshot\n")
    runs = {}
    for platform, run_name in platforms:
        run_dir, report = _write_lane_run(
            reports, phase_id, run_name, platform, check_ids, tmp_path,
        )
        runs[platform] = (run_dir, report)
    return manifest_path, reports, runs


def test_lane_selection_picks_platform_specific_report(tmp_path, verifier):
    manifest_path, reports, _ = _multi_lane_fixture(
        tmp_path, "P03", ["P03.memory"],
        [("macos", "run-mac-001"), ("windows", "run-win-001")],
        milestones=[],
    )
    code, stdout, stderr = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports),
         "--phase", "P03", "--json"],
    )
    assert code == 0, stderr
    data = json.loads(stdout)
    lanes = data["phases"][0]["lanes"]
    assert set(lanes) == {"macos", "windows"}
    assert lanes["macos"]["run_id"] == "run-mac-001"
    assert lanes["windows"]["run_id"] == "run-win-001"
    assert lanes["macos"]["result"] == "pass"
    assert lanes["windows"]["result"] == "pass"


def test_any_one_desktop_milestone_passes_with_one_lane(tmp_path, verifier):
    milestones = [{
        "id": "first_desktop_feature",
        "required_checks": ["P06.baseline", "P06.readback"],
        "lane_policy": "any_one_desktop_lane",
    }]
    manifest_path, reports, _ = _multi_lane_fixture(
        tmp_path, "P06", ["P06.baseline", "P06.readback"],
        [("macos", "run-mac-001")],
        milestones=milestones,
    )
    code, stdout, stderr = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports), "--json"],
    )
    assert code == 0, stderr
    data = json.loads(stdout)
    milestone = next(m for m in data["milestones"] if m["id"] == "first_desktop_feature")
    assert milestone["result"] == "pass", stderr


def test_both_platform_milestone_blocked_with_missing_lane(tmp_path, verifier):
    milestones = [{
        "id": "full_scope",
        "required_phases": ["P10"],
        "required_checks": ["P10.mobile_integration"],
        "lane_policy": "P10_both_desktop_lanes_and_P09_qualified_android_lane",
    }]
    manifest_path, reports, _ = _multi_lane_fixture(
        tmp_path, "P10", ["P10.mobile_integration"],
        [("macos", "run-mac-001")],
        milestones=milestones,
    )
    code, stdout, _ = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports), "--json"],
    )
    assert code == 2
    data = json.loads(stdout)
    milestone = next(m for m in data["milestones"] if m["id"] == "full_scope")
    assert milestone["result"] == "blocked"


def test_prerequisite_report_hash_mismatch_blocks_downstream(tmp_path, verifier):
    # Build a shared P00 and a P01 depending on it, with wrong prereq hash.
    manifest_path, reports = _write_passing_phase(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["phases"].append({
        "id": "P01", "title": "Knowledge", "owner_role": "K", "reviewer_role": "V",
        "depends_on": ["P00"], "lanes": ["shared"], "expected_outcome": "ok",
        "required_outputs": [], "checks": [{
            "id": "P01.retrieval", "evidence_class": "automated_or_reviewed_artifact",
            "required_for_phase": True, "result": "not_run", "report_ref": None,
        }],
    })
    manifest_path.write_text(json.dumps(manifest))
    p01_dir = reports / "P01" / "run-001"
    p01_dir.mkdir(parents=True)
    ref = _write_artifact(p01_dir, "retrieval.log")
    check = _base_check(check_id="P01.retrieval", artifact_refs=[ref])
    p01_report = _base_report(p01_dir, check)
    p01_report["phase_id"] = "P01"
    # Deliberately wrong prereq binding: phase_report of P00 with wrong sha.
    p01_report["input_artifacts"].append({
        "path": "../../P00/run-001/report.json",
        "sha256": "0" * 64, "kind": "phase_report",
        "phase_id": "P00",
        "implementation_revision": p01_report["implementation_revision"],
    })
    (p01_dir / "report.json").write_text(json.dumps(p01_report))
    code, _, stderr = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports),
         "--phase", "P01"],
    )
    assert code == 1
    assert "phase_report" in stderr or "prerequisite" in stderr or "sha256 mismatch" in stderr


def test_conditional_mobile_check_not_required_without_flag(tmp_path, verifier):
    manifest = _base_manifest(extra_checks=[{
        "id": "P00.mobile_integration", "required_for_phase": False,
        "required_when": "mobile_support_advertised_or_full_scope_acceptance",
        "result": "not_run", "report_ref": None,
        "evidence_class": "automated_or_reviewed_artifact",
    }])
    manifest_path, reports = _write_passing_phase(tmp_path, manifest)
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    report["mobile_support_advertised"] = False
    report_path.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    # Without advertising flag and with report=False, conditional check is not required.
    assert code == 0, stderr


def test_conditional_mobile_check_required_with_flag_and_missing_report_blocks(tmp_path, verifier):
    manifest = _base_manifest(extra_checks=[{
        "id": "P00.mobile_integration", "required_for_phase": False,
        "required_when": "mobile_support_advertised_or_full_scope_acceptance",
        "result": "not_run", "report_ref": None,
        "evidence_class": "automated_or_reviewed_artifact",
    }])
    manifest_path, reports = _write_passing_phase(tmp_path, manifest)
    # Report declares False, CLI flag overrides to True.
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    report["mobile_support_advertised"] = False
    report_path.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports),
         "--mobile-support-advertised"],
    )
    # Flag forces the check required; missing check block the phase.
    assert code == 2, stderr
    assert "P00.mobile_integration" in stderr


def test_phase_manifest_contract_digest_detects_stale_report(verifier):
    phase = {
        "id": "P00", "title": "X", "checks": [
            {"id": "P00.a", "evidence_class": "automated_or_reviewed_artifact",
             "required_for_phase": True, "result": "not_run"},
        ],
        "lanes": ["shared"],
    }
    original = verifier.phase_manifest_contract_digest(phase)
    changed = json.loads(json.dumps(phase))
    changed["checks"][0]["evidence_class"] = "live_target_or_external_fixture"
    assert verifier.phase_manifest_contract_digest(changed) != original


def test_phase_manifest_contract_digest_ignores_progress_changes(verifier):
    phase = {
        "id": "P00", "title": "X", "checks": [
            {"id": "P00.a", "evidence_class": "automated_or_reviewed_artifact",
             "required_for_phase": True, "result": "not_run", "report_ref": None},
        ],
        "lanes": ["shared"],
        "implementation_status": "not_started",
        "lane_results": {}, "review_report": None, "review_notes": None,
    }
    digest = verifier.phase_manifest_contract_digest(phase)
    updated = json.loads(json.dumps(phase))
    updated["implementation_status"] = "verified"
    updated["lane_results"] = {"shared": "verified"}
    updated["review_report"] = "artifacts/X/run-001/report.json"
    updated["review_notes"] = "looks good"
    updated["checks"][0]["result"] = "pass"
    updated["checks"][0]["report_ref"] = "x/y"
    assert verifier.phase_manifest_contract_digest(updated) == digest


def test_source_snapshot_missing_path_blocks(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["source_paths"] = ["source.py", "second.py"]
    (tmp_path / "second.py").write_text("# second file\n")
    manifest_path, reports = _write_passing_phase(tmp_path, manifest)
    # Report snapshots do not include second.py.
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 1
    assert "second.py" in stderr


def test_source_snapshot_complete_passes(tmp_path, verifier):
    manifest = _base_manifest()
    manifest["phases"][0]["source_paths"] = ["source.py", "second.py"]
    second = tmp_path / "second.py"
    second.write_text("# second file\n")
    manifest_path, reports = _write_passing_phase(tmp_path, manifest)
    # Add second.py snapshot into both input_artifacts and artifacts.
    report_path = reports / "P00/run-001/report.json"
    report = json.loads(report_path.read_text())
    second_ref = {"path": "second.py", "sha256": _sha256(second.read_bytes()),
                  "kind": "source_snapshot"}
    report["input_artifacts"].insert(1, second_ref)
    report["artifacts"].append(second_ref)
    # Recompute the source snapshot digest revision.
    lines = sorted([
        f"{report['artifacts'][0]['path']}\t{report['artifacts'][0]['sha256'].lower()}",
        f"{second_ref['path']}\t{second_ref['sha256'].lower()}",
    ])
    rev = "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()
    report["implementation_revision"] = rev
    report["reviewed_revision"] = rev
    report_path.write_text(json.dumps(report))
    code, _, stderr = _run(
        verifier, ["--manifest", str(manifest_path), "--reports", str(reports)]
    )
    assert code == 0, stderr


def test_release_os_cli_binds_named_lane(tmp_path, verifier):
    milestones = [{
        "id": "desktop_release",
        "required_phases": ["P10"],
        "lane_policy": "named_desktop_lane",
    }]
    manifest_path, reports, _ = _multi_lane_fixture(
        tmp_path, "P10", ["P10.regression"],
        [("macos", "run-mac-001")],
        milestones=milestones,
    )
    code_without, stdout_without, _ = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports), "--json"],
    )
    data = json.loads(stdout_without)
    milestone = next(m for m in data["milestones"] if m["id"] == "desktop_release")
    assert milestone["result"] == "blocked"
    code_mac, stdout_mac, _ = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports),
         "--release-os", "macos", "--json"],
    )
    data = json.loads(stdout_mac)
    milestone = next(m for m in data["milestones"] if m["id"] == "desktop_release")
    assert milestone["result"] == "pass"
    code_win, stdout_win, _ = _run(
        verifier,
        ["--manifest", str(manifest_path), "--reports", str(reports),
         "--release-os", "windows", "--json"],
    )
    data = json.loads(stdout_win)
    milestone = next(m for m in data["milestones"] if m["id"] == "desktop_release")
    assert milestone["result"] in ("blocked", "not_run")


@pytest.mark.parametrize('mutation', ['missing_review', 'bad_source_hash', 'missing_prerequisite'])
@pytest.mark.parametrize('required_phases', [False, True])
def test_milestone_rejects_ineligible_lane_evidence(tmp_path, verifier, mutation, required_phases):
    source = _desktop_phase_dict('P06', ['P06.behavior'])
    if mutation == 'missing_prerequisite':
        source['depends_on'] = ['P00']
    downstream = _desktop_phase_dict('P07', ['P07.projection'], {
        'milestone_dependencies': ['first_desktop_feature'],
    })
    manifest = _base_manifest(extra_phases=[source, downstream])
    milestone = {'id': 'first_desktop_feature', 'lane_policy': 'any_one_desktop_lane'}
    milestone['required_phases' if required_phases else 'required_checks'] = (
        ['P06'] if required_phases else ['P06.behavior']
    )
    manifest['milestones'] = [milestone]
    path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / 'reports'
    run, report = _write_lane_run(reports, 'P06', 'run-mac-001', 'macos', ['P06.behavior'], tmp_path)
    if mutation == 'missing_review':
        for key in ('reviewer', 'reviewed_revision', 'independent_replay'):
            report[key] = None
    elif mutation == 'bad_source_hash':
        report['artifacts'][0]['sha256'] = '0' * 64
    (run / 'report.json').write_text(json.dumps(report))
    _write_lane_run(reports, 'P07', 'run-mac-001', 'macos', ['P07.projection'], tmp_path)
    code, out, _ = _run(verifier, ['--manifest', str(path), '--reports', str(reports), '--phase', 'P07', '--json'])
    assert code != 0
    assert json.loads(out)['milestones'][0]['result'] != 'pass'


def test_negative_cli_flag_cannot_hide_advertised_mobile(tmp_path, verifier):
    manifest = _base_manifest(extra_checks=[{
        'id': 'P00.mobile_integration', 'required_for_phase': False,
        'required_when': 'mobile_support_advertised_or_full_scope_acceptance',
        'evidence_class': 'automated_or_reviewed_artifact',
    }])
    path, reports = _write_passing_phase(tmp_path, manifest=manifest)
    report_path = reports / 'P00/run-001/report.json'
    report = json.loads(report_path.read_text())
    report['mobile_support_advertised'] = True
    report_path.write_text(json.dumps(report))
    code, _, stderr = _run(verifier, ['--manifest', str(path), '--reports', str(reports), '--phase', 'P00', '--no-mobile-support-advertised'])
    assert code != 0
    assert 'P00.mobile_integration' in stderr


@pytest.mark.parametrize('policy', ['any_one_desktop_lane', 'named_desktop_lane', 'same_exact_target', 'same_exact_android_target'])
def test_check_milestone_rejects_ineligible_report_for_every_policy(verifier, policy):
    lane = 'android' if policy == 'same_exact_android_target' else 'macos'
    state = {'result': 'fail', 'evidence_eligible': False,
             'report': {'target_identity': {'platform': lane, 'device_id': 'device', 'build_fingerprint': {'build': 'one'}}},
             'check_statuses': {'P02.identity': 'pass'}}
    result = verifier._evaluate_milestone(
        {'lane_policy': policy, 'required_checks': ['P02.identity']},
        {'P02': 'fail'}, {}, lambda _: None,
        phase_lanes={'P02': {lane: state}}, release_os=lane,
    )
    assert result != 'pass'


def test_optional_target_checks_can_qualify_with_unrelated_check_blocked(verifier):
    state = {'result': 'blocked', 'evidence_eligible': True,
             'report': {'target_identity': {'platform': 'macos', 'device_id': 'device', 'build_fingerprint': {'build': 'one'}}},
             'check_statuses': {'P02.identity': 'pass', 'P02.target_memory_access': 'pass', 'P02.other': 'blocked'}}
    assert verifier._evaluate_milestone(
        {'lane_policy': 'same_exact_target', 'required_checks': ['P02.identity', 'P02.target_memory_access']},
        {'P02': 'blocked'}, {}, lambda _: None, phase_lanes={'P02': {'macos': state}},
    ) == 'pass'


@pytest.mark.parametrize('wrong_lane_binding', [False, True])
def test_lane_prerequisites_bind_matching_lane_without_other_lane_downgrade(tmp_path, verifier, wrong_lane_binding):
    source = _desktop_phase_dict('P04', ['P04.controller'])
    downstream = _desktop_phase_dict('P06', ['P06.behavior'], {'depends_on': ['P04']})
    manifest = _base_manifest(extra_phases=[source, downstream])
    manifest['milestones'] = [{'id': 'feature', 'lane_policy': 'any_one_desktop_lane', 'required_checks': ['P06.behavior']}]
    path = _write_manifest(tmp_path, manifest)
    reports = tmp_path / 'reports'
    mac_run, mac_report = _write_lane_run(reports, 'P04', 'run-mac-001', 'macos', ['P04.controller'], tmp_path)
    win_run, win_report = _write_lane_run(reports, 'P04', 'run-win-001', 'windows', ['P04.controller'], tmp_path)
    # A valid Mac prerequisite remains usable even if the other lane is unreviewed.
    win_report['reviewer'] = None
    win_report['reviewed_revision'] = None
    win_report['independent_replay'] = None
    (win_run / 'report.json').write_text(json.dumps(win_report))
    run, report = _write_lane_run(reports, 'P06', 'run-mac-001', 'macos', ['P06.behavior'], tmp_path)
    upstream_run, upstream_report = (win_run, win_report) if wrong_lane_binding else (mac_run, mac_report)
    ref = {'path': str(upstream_run / 'report.json'), 'sha256': _sha256((upstream_run / 'report.json').read_bytes()),
                'kind': 'phase_report', 'phase_id': 'P04', 'implementation_revision': upstream_report['implementation_revision']}
    report['input_artifacts'].append(ref)
    (run / 'report.json').write_text(json.dumps(report))
    code, out, stderr = _run(verifier, ['--manifest', str(path), '--reports', str(reports), '--phase', 'P06', '--json'])
    data = json.loads(out)
    if wrong_lane_binding:
        assert code != 0
        assert data['milestones'][0]['result'] != 'pass'
    else:
        assert code == 0, stderr
        assert data['milestones'][0]['result'] == 'pass'


@pytest.mark.parametrize('policy', ['any_one_desktop_lane', 'same_exact_target', 'same_exact_android_target'])
def test_mixed_phase_and_check_milestone_honors_required_phase(verifier, policy):
    lane = 'android' if policy == 'same_exact_android_target' else 'macos'
    identity = {'platform': lane, 'device_id': 'device', 'build_fingerprint': {'build': 'one'}}
    lanes = {
        'P02': {lane: {'result': 'pass', 'report': {'target_identity': identity}, 'check_statuses': {'P02.identity': 'pass'}}},
        'P08': {lane: {'result': 'blocked', 'report': {'target_identity': identity}, 'check_statuses': {}}},
    }
    assert verifier._evaluate_milestone(
        {'lane_policy': policy, 'required_phases': ['P08'], 'required_checks': ['P02.identity']},
        {'P02': 'pass', 'P08': 'blocked'}, {}, lambda _: None, phase_lanes=lanes,
    ) != 'pass'


def test_same_target_phase_only_milestone_requires_matching_identity(verifier):
    def state(device):
        return {'result': 'pass', 'report': {'target_identity': {
            'platform': 'android', 'device_id': device, 'build_fingerprint': {'build': 'one'}}}}
    milestone = {'lane_policy': 'same_exact_android_target', 'required_phases': ['P02', 'P08']}
    lanes = {'P02': {'android': state('device')}, 'P08': {'android': state('device')}}
    assert verifier._evaluate_milestone(milestone, {}, {}, lambda _: None, phase_lanes=lanes) == 'pass'
    lanes['P08']['android'] = state('other-device')
    assert verifier._evaluate_milestone(milestone, {}, {}, lambda _: None, phase_lanes=lanes) != 'pass'
