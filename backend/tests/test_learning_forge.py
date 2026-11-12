"""Forge pipeline tests: trace mining, repo mining, bundle writing, dedup."""
import json
import pytest
from pathlib import Path

from bughunter.app import Application
from bughunter.learning_forge import (
    LearningForge, TraceMiner, ErrorMiner, RepoMiner,
    SFTPair, DPOPair,
)


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def _seed_run(app, status, prompt, transcript):
    app.store.put("run", {"status": status, "prompt": prompt,
                          "storable_transcript": transcript,
                          "kind": "investigation"})


def test_trace_miner_yields_completed(app):
    _seed_run(app, "completed", "show me the login flow", [
        {"role": "user", "content": "show me the login flow"},
        {"role": "assistant", "content": "The login flow posts /login."},
    ])
    _seed_run(app, "failed", "irrelevant", [
        {"role": "user", "content": "x"},
    ])
    pairs = list(TraceMiner(app.store).iter_pairs())
    assert len(pairs) == 1
    assert pairs[0].domain in {"web-audit", "network", "memory",
                                "mobile", "binary-re", "osint"}
    assert pairs[0].messages[0]["content"].startswith("show me")
    assert pairs[0].hash is not None


def test_error_miner_pairs_failure_with_success(app):
    _seed_run(app, "completed", "nmap scan foo.com", [
        {"role": "user", "content": "nmap scan foo.com"},
        {"role": "assistant", "content": "ran -sV, found 22/80/443"},
    ])
    _seed_run(app, "failed", "nmap scan foo.com", [
        {"role": "user", "content": "nmap scan foo.com"},
        {"role": "assistant", "content": "I can't scan that."},
    ])
    pairs = list(ErrorMiner(app.store).iter_pairs())
    assert len(pairs) == 1
    assert "nmap" in pairs[0].prompt.lower()
    assert pairs[0].chosen != pairs[0].rejected


def test_repo_miner_covers_python(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "alpha.py").write_text('"""Doc.\n"""\n'
                                           'def foo():\n    return 1\n' * 20)
    pairs = list(RepoMiner(repo).iter_pairs())
    # at least one explain pair (plus optional FIM)
    kinds = {tuple(p.tags) for p in pairs}
    assert any("explain" in t for t in kinds)


def test_forge_build_produces_bundle(app, tmp_path):
    _seed_run(app, "completed", "list open ports on fixture", [
        {"role": "user", "content": "list open ports"},
        {"role": "assistant", "content": "22, 80"},
    ])
    forge = LearningForge(app)
    bundle = forge.build(include=["traces"], name="unit")
    data_dir = Path(bundle["data_dir"])
    assert data_dir.exists()
    assert (data_dir / "train.jsonl").exists()
    assert (data_dir / "manifest.json").exists()
    manifest = json.loads((data_dir / "manifest.json").read_text())
    assert manifest["counts"]["sft"] >= 1


def test_forge_dedupe_same_transcript(app):
    _seed_run(app, "completed", "x", [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
    ])
    _seed_run(app, "completed", "x", [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
    ])
    forge = LearningForge(app)
    bundle = forge.build(include=["traces"])
    train = Path(bundle["data_dir"]) / "train.jsonl"
    # Two identical runs → one line (valid.jsonl may steal the only line
    # if a holdout is possible; but with <50 lines no holdout is cut).
    lines = [l for l in train.read_text().splitlines() if l]
    assert len(lines) == 1
