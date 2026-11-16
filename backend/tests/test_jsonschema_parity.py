"""Shared-fixture parity check through the JSON Schema library.

Drives the SAME parity fixture bank the Python/Rust/TS parity tests use,
but through the authoritative JSON Schemas rather than any binding. Any
disagreement between the schemas and the record-level validators means
the schemas (the source of truth) have drifted.

If ``jsonschema`` is not installed, the test is SKIPPED with a reason.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema", reason="jsonschema library not installed; add to [project.optional-dependencies.dev]")


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts" / "game_runtime"
PARITY = CONTRACTS / "fixtures" / "parity"

KIND_FILES = {
    "game_target": "game_target.schema.json",
    "game_session": "game_session.schema.json",
    "mapping_recipe": "mapping_recipe.schema.json",
    "native_operation": "native_operation.schema.json",
    "feature_run": "feature_run.schema.json",
}


def _load_schema(kind: str) -> dict:
    return json.loads((CONTRACTS / KIND_FILES[kind]).read_text())


def _kind_from_stem(stem: str) -> str:
    return stem.split("__", 1)[0]


def _validator(kind: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(_load_schema(kind))


def _list_json(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix == ".json")


def test_schemas_parse_and_validate_themselves() -> None:
    for kind, filename in KIND_FILES.items():
        schema = _load_schema(kind)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema.get("title"), f"{filename} must have a title"


def test_every_valid_parity_fixture_validates() -> None:
    files = _list_json(PARITY / "valid")
    assert len(files) >= 5, f"need ≥5 valid fixtures; found {len(files)}"
    for file in files:
        kind = _kind_from_stem(file.stem)
        data = json.loads(file.read_text())
        errors = sorted(_validator(kind).iter_errors(data), key=lambda e: e.path)
        assert not errors, (
            f"valid fixture {file.name} should pass JSON Schema but got: "
            f"{[(e.message, list(e.absolute_path)) for e in errors]}"
        )


def test_every_invalid_parity_fixture_rejects() -> None:
    files = _list_json(PARITY / "invalid")
    assert len(files) >= 5, f"need ≥5 invalid fixtures; found {len(files)}"
    for file in files:
        kind = _kind_from_stem(file.stem)
        data = json.loads(file.read_text())
        errors = list(_validator(kind).iter_errors(data))
        assert errors, (
            f"invalid fixture {file.name} should be rejected by JSON Schema "
            f"but it validated clean"
        )


def test_five_reproduced_mismatches_are_rejected() -> None:
    """The five records Codex named as previously-accepted-by-Python.

    They must now be rejected by the JSON Schemas directly (not only by
    the Python bindings' custom validators).
    """
    stems = [
        "mapping_recipe__revision_zero",
        "mapping_recipe__revision_bool",
        "mapping_recipe__revision_string",
        "mapping_recipe__schema_version_negative",
        "game_target__missing_modules_and_capabilities",
    ]
    for stem in stems:
        file = PARITY / "invalid" / f"{stem}.json"
        assert file.is_file(), f"parity fixture {stem}.json missing"
        kind = _kind_from_stem(stem)
        data = json.loads(file.read_text())
        errors = list(_validator(kind).iter_errors(data))
        assert errors, f"JSON Schema must reject {stem}; got no errors"
