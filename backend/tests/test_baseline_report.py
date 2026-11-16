"""P00.baseline report shape and secret-scan checks.

These assertions validate the report produced by
artifacts/game-verification/game-runtime-v1/P00/baseline/run.sh.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "artifacts" / "game-verification" / "game-runtime-v1" / "P00" / "baseline"
REPORT = BASE_DIR / "report.json"

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

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"baseline report not produced yet: {REPORT}")
    with REPORT.open() as f:
        return json.load(f)


def test_required_top_level_keys(report):
    missing = [k for k in REPORT_REQUIRED_FIELDS if k not in report]
    assert not missing, f"missing report fields: {missing}"


def test_checks_shape(report):
    assert isinstance(report["checks"], list) and report["checks"], "checks must be a non-empty list"
    for check in report["checks"]:
        missing = [k for k in CHECK_REQUIRED_FIELDS if k not in check]
        assert not missing, f"check {check.get('id')} missing fields: {missing}"
        assert check["result"] in {"pass", "fail", "blocked", "not_run"}


def test_aggregate_result_enum(report):
    assert report.get("aggregate_result") in {"pass", "fail", "blocked"}


def test_pass_steps_have_zero_exit(report):
    for step in report.get("steps", []):
        if step.get("status") == "pass":
            assert step.get("exit_code") == 0, (
                f"step {step.get('step')} marked pass with exit_code {step.get('exit_code')}"
            )


def test_no_api_keys_in_logs(report):
    log_dir = BASE_DIR / "logs"
    if not log_dir.exists():
        return
    for log_file in log_dir.glob("*.log"):
        text = log_file.read_text(errors="replace")
        for pat in SECRET_PATTERNS:
            matches = pat.findall(text)
            assert not matches, f"secret-like token found in {log_file.name}: {matches[:1]}"
    report_text = REPORT.read_text(errors="replace")
    for pat in SECRET_PATTERNS:
        assert not pat.search(report_text), f"secret-like token found in report.json via pattern {pat.pattern}"
