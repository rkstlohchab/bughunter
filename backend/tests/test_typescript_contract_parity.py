"""Shared-fixture parity check through the TypeScript (Node.mjs) validator.

Executes ``node --test src/types/gameRuntimeValidator.test.mjs`` from the
repository root. The .mjs test walks the same parity fixture bank under
``contracts/game_runtime/fixtures/parity/`` that the Python and Rust
parity tests walk. Any disagreement between the TS validator and the
JSON Schemas/other bindings surfaces here.

If ``node`` is not on PATH, the test is SKIPPED (not passed) with a
reason naming the missing dependency.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TS_TEST = REPO_ROOT / "src" / "types" / "gameRuntimeValidator.test.mjs"


@pytest.fixture(scope="module")
def node_binary() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not on PATH; install Node 20+ to run TypeScript parity")
    return binary


def _run_ts_test(node: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node, "--test", str(TS_TEST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )


def test_typescript_validator_file_exists() -> None:
    assert TS_TEST.is_file(), f"missing TS parity test at {TS_TEST}"
    sibling = TS_TEST.with_name("gameRuntimeValidator.mjs")
    assert sibling.is_file(), f"missing TS validator at {sibling}"


def test_typescript_parity_suite_passes(node_binary: str) -> None:
    result = _run_ts_test(node_binary)
    if result.returncode != 0:
        raise AssertionError(
            f"TS parity suite failed with exit={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    # Sanity: the TAP output must mention all five of our named tests.
    expected_titles = [
        "CONTRACT_VERSION is 1",
        "KINDS exposes the five records",
        "every valid parity fixture is accepted",
        "every invalid parity fixture is rejected with the expected substring",
        "reproduced Python-vs-schema mismatches are rejected",
    ]
    stdout = result.stdout
    missing = [title for title in expected_titles if title not in stdout]
    assert not missing, f"TS parity output missing expected test names: {missing}\nstdout:\n{stdout}"
