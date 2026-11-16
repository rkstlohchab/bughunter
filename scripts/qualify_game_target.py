#!/usr/bin/env python3
"""P02 target qualification CLI.

Produces ``target_qualification.json`` for one game target:

* identity: the :class:`GameTarget` record (validated against the wire
  contract and cross-field identity rules).
* static_analysis: a Cpp2IL output summary when the pinned tool is
  present, otherwise an honest ``unsupported_tool`` row that records
  the pinned version the operator needs to install.
* runtime_access: ``static_only``, ``runtime_access_blocked`` or
  ``runtime_ready`` from a bounded read attempt. A spawned PID alone
  does not satisfy ``runtime_ready``.
* unresolved_inputs: every pin that could not be satisfied, every
  capability still blocked, every tool missing.

Usage::

    qualify_game_target.py --target <record-json-or-game-id> \
                           --report-dir <dir>

``--target`` accepts a game id (``among_us``) to resolve against the
local inventory, or a path to a JSON file containing a GameTarget
record. ``--report-dir`` must be an existing directory; the report is
written to ``<report-dir>/target_qualification.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bughunter.game_runtime import GameTarget, validate_record  # noqa: E402
from bughunter.game_runtime.targets import (  # noqa: E402
    PINNED_TOOLS,
    discover_installed,
    enforce_identity,
    probe_runtime_access,
    rea_tool_discovery,
    verify_target_files,
)


def _load_target(spec: str) -> GameTarget:
    """Resolve ``--target`` to a validated GameTarget.

    ``spec`` is either a path to a JSON file, or a game id to match
    against discovered inventory.
    """
    path = Path(spec)
    if path.is_file():
        data = json.loads(path.read_text())
        validate_record("game_target", data)
        return enforce_identity(GameTarget.model_validate(data))

    inventory = discover_installed()
    matches = [t for t in inventory if t.game_id == spec or t.id == spec]
    if not matches:
        known = sorted({t.game_id for t in inventory})
        raise SystemExit(
            f"no installed target matches {spec!r}; "
            f"known game ids: {known or '(none discovered)'}"
        )
    if len(matches) > 1:
        ids = [t.id for t in matches]
        raise SystemExit(
            f"spec {spec!r} is ambiguous; pass one of these target ids: {ids}"
        )
    return enforce_identity(matches[0])


# GitHub release asset digests, 2022.1.0-pre-release.21. The original
# executable is checked before an isolated copy is locally signed on macOS.
CPP2IL_DIGESTS = {
    "6670ddb93f28d4e7f329251d2597d2abbc6ba23e46382f7ca7b5e437ec93606a": "OSX-ARM64",
    "15faab020698512807f792aef32a89fe529d41d2cc03955dd3516f0519fe8f72": "OSX",
    "526998e593c52c029c5a6215c5c6c9f9d963706bfc409fc9ff80a95c4c500349": "Linux",
    "2d8e76b11f52fd85440ec60abf87d5c3455ea54f5104e0af51da15ba4440871b": "Linux-ARM64",
    "663fb432433b4371fd1ee0ebc321a8fff2a9aac5ac4230c843f9e03ddee4e04c": "Windows",
    "8986f219b5992f9290cb13e1f49431756cbd8865f1cf1f5a38ba61acc27cc1fb": "Windows-ARM64",
}


def _static_analysis(target: GameTarget, measured_inputs, *, tool_path=None, report_dir=None):
    row = {"tool": "cpp2il", "pinned_version": PINNED_TOOLS["cpp2il"]["version"],
           "pin_source": PINNED_TOOLS["cpp2il"]["url"], "status": "unsupported_tool"}
    candidate = tool_path or shutil.which("Cpp2IL") or shutil.which("cpp2il")
    if candidate is None:
        row["detail"] = "Pinned Cpp2IL executable unavailable; provide --cpp2il with the verified release asset."
        return row
    original = Path(candidate).resolve()
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    row.update(original_path=str(original), original_sha256=digest)
    if digest not in CPP2IL_DIGESTS:
        row.update(status="tool_error", detail="Cpp2IL executable SHA-256 does not match the pinned release assets")
        return row
    row["release_asset"] = CPP2IL_DIGESTS[digest]
    if report_dir is None:
        row.update(status="not_qualified", detail="Static derivation needs a report directory")
        return row
    work = Path(report_dir).resolve() / "static-analysis"
    work.mkdir(exist_ok=False)
    executable = work / ("Cpp2IL.exe" if sys.platform == "win32" else "Cpp2IL")
    shutil.copyfile(original, executable)
    executable.chmod(0o700)
    if sys.platform == "darwin":
        command = ["codesign", "--force", "--sign", "-", str(executable)]
        signed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        row["local_signing"] = {"command": command, "exit_code": signed.returncode,
                                "diagnostic": signed.stderr[:1500]}
        if signed.returncode:
            row.update(status="tool_error", detail="Local signing of verified tool copy failed")
            return row
    row["executed_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    by_role = {r["role"]: r for r in measured_inputs}
    binary = by_role.get("UnityFramework") or next((r for r in measured_inputs if r["role"] not in ("main", "metadata", "unity_version")), None)
    metadata = by_role.get("metadata")
    unity_version = target.build_fingerprint.extras.get("unity_version")
    if not binary or not metadata or not unity_version:
        row.update(status="not_qualified", detail="Missing measured IL2CPP module, metadata, or Unity version")
        return row
    output = work / "derived"
    command = [str(executable), "--force-binary-path", binary["path"],
               "--force-metadata-path", metadata["path"], "--force-unity-version", unity_version,
               "--output-as", "diffable-cs", "--output-to", str(output)]
    log = work / "cpp2il.log"
    row["command"] = command
    try:
        with log.open("w") as stream:
            run = subprocess.run(command, cwd=work, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        code = run.returncode
    except subprocess.TimeoutExpired:
        code = 124
    row.update(exit_code=code, log={"path": str(log), "sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
    row["outputs"] = [{"path": str(p.relative_to(work)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                       "size": p.stat().st_size} for p in sorted(output.rglob("*")) if p.is_file()] if output.exists() else []
    # Recheck bytes after the parser exits to detect target update races.
    unchanged = verify_target_files(target) == measured_inputs
    if code == 0 and row["outputs"] and unchanged:
        row.update(status="static_candidates_derived", detail="Pinned parser produced candidate layouts; runtime validation remains separate.")
    else:
        row.update(status="not_qualified", detail="Pinned parser did not produce verified static output; inspect the captured log.")
    return row


def _hash_target(target: GameTarget) -> str:
    """Stable hash of the target identity, used as an audit anchor."""
    blob = target.model_dump_json().encode()
    return hashlib.sha256(blob).hexdigest()


def qualify(target: GameTarget, *, attempt_runtime: bool = True, tool_path=None, report_dir=None, rea_service=None) -> dict[str, Any]:
    measured_inputs = verify_target_files(target)
    static = _static_analysis(target, measured_inputs, tool_path=tool_path, report_dir=report_dir)
    runtime = probe_runtime_access(target, attempt=attempt_runtime)
    tools = rea_tool_discovery(rea_service)
    identity = target.model_dump()
    validate_record("game_target", identity)  # paranoia gate

    unresolved: list[dict[str, str]] = []
    if static.get("status") in ("unsupported_tool", "tool_error", "not_qualified"):
        unresolved.append({
            "kind": "tool",
            "tool": "cpp2il",
            "detail": static.get("detail", ""),
            "pinned_version": static["pinned_version"],
        })
    if runtime.status != "runtime_ready":
        unresolved.append({
            "kind": "runtime_access",
            "status": runtime.status,
            "detail": runtime.reason,
        })
    for name, probe in tools.get("path_probes", {}).items():
        if not probe.get("found") and probe.get("pinned_version"):
            unresolved.append({
                "kind": "tool",
                "tool": name,
                "detail": "not found on PATH",
                "pinned_version": probe["pinned_version"],
            })

    report = {
        "schema_version": 1,
        "report_kind": "target_qualification",
        "identity": identity,
        "identity_hash": _hash_target(target),
        "measured_inputs": measured_inputs,
        "static_analysis": static,
        "runtime_access": {
            "status": runtime.status,
            "reason": runtime.reason,
            "pid": runtime.pid,
            "access_mode": runtime.access_mode,
            "bytes_read": runtime.bytes_read,
            "sample_sha256": runtime.sample_sha256,
            "process_start_time_ns": runtime.process_start_time_ns,
        },
        "tooling": tools,
        "unresolved_inputs": unresolved,
    }
    return report


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qualify_game_target.py",
        description="P02 target qualification CLI (see docs/game-lab/agent-implementation-plan.md).",
    )
    parser.add_argument(
        "--target", required=True,
        help="GameTarget record JSON path, or a game_id to match discovered inventory.",
    )
    parser.add_argument(
        "--report-dir", required=True,
        help="Directory to write target_qualification.json into.",
    )
    parser.add_argument(
        "--no-runtime-attempt", action="store_true",
        help="Skip the bounded runtime read attempt; forces runtime_access=static_only.",
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="Pretty-print the report JSON (default: compact).",
    )
    parser.add_argument("--cpp2il", help="Path to an original pinned Cpp2IL release executable")
    parser.add_argument("--rea-catalog", action="store_true", help="Query the configured REA integration for its live catalog")
    args = parser.parse_args(argv)

    report_dir = Path(args.report_dir)
    if not report_dir.is_dir():
        parser.error(f"--report-dir {report_dir} does not exist")

    try:
        target = _load_target(args.target)
        verify_target_files(target)
    except (ValueError, OSError) as exc:
        print(f"target qualification rejected: {exc}", file=sys.stderr)
        return 2
    rea_service = None
    if args.rea_catalog:
        from bughunter.rea import ReaService
        from bughunter.store import Store
        rea_service = ReaService(Store())
    report = qualify(target, attempt_runtime=not args.no_runtime_attempt, tool_path=args.cpp2il, report_dir=report_dir, rea_service=rea_service)

    out_path = report_dir / "target_qualification.json"
    out_path.write_text(
        json.dumps(report, indent=2 if args.pretty else None, sort_keys=False) + "\n"
    )
    # Echo the report location, not the body: stdout noise is friction
    # the downstream verifier does not need.
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
