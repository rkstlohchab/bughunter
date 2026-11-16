"""Phase P00 contract tests.

Validates that the Python, JSON Schema, TypeScript and Rust wire contracts
agree on record names, enum values and required fields, and that the
Pydantic validator rejects the invalid fixtures in ``contracts/game_runtime``
with the documented substrings.

The ``jsonschema`` library is not a backend dependency; these tests check
the schema files parse as JSON and that their ``$schema``/``$id``/``required``
sets match the Pydantic models. A future suite can add structural
validation once ``jsonschema`` is pinned.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bughunter.game_runtime import (
    CONTRACT_VERSION,
    AddressRepr,
    FeatureRun,
    GameSession,
    GameTarget,
    MappingRecipe,
    NativeOperation,
    NativeOpState,
    TRANSITIONS,
    can_transition,
    validate,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts" / "game_runtime"
FIXTURE_VALID = CONTRACTS_DIR / "fixtures" / "valid"
FIXTURE_INVALID = CONTRACTS_DIR / "fixtures" / "invalid"

MODEL_BY_KIND = {
    "game_target": GameTarget,
    "game_session": GameSession,
    "mapping_recipe": MappingRecipe,
    "native_operation": NativeOperation,
    "feature_run": FeatureRun,
}

SCHEMA_FILES = {
    "game_target": "game_target.schema.json",
    "game_session": "game_session.schema.json",
    "mapping_recipe": "mapping_recipe.schema.json",
    "native_operation": "native_operation.schema.json",
    "feature_run": "feature_run.schema.json",
}


# ---------------------------------------------------------------------------
# Valid fixtures: each loads, round-trips, and validate() accepts it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(MODEL_BY_KIND))
def test_valid_fixture_loads_and_round_trips(kind: str) -> None:
    path = FIXTURE_VALID / f"{kind}.json"
    data = json.loads(path.read_text())
    assert validate(kind, data) == [], f"expected valid: {validate(kind, data)}"
    model = MODEL_BY_KIND[kind]
    instance = model.model_validate(data)
    serialised = instance.model_dump_json()
    round_tripped = model.model_validate_json(serialised)
    assert round_tripped == instance


# ---------------------------------------------------------------------------
# Invalid fixtures: rejected AND expected substring appears.
# ---------------------------------------------------------------------------


def _collect_invalid_fixtures() -> list[tuple[str, Path, Path]]:
    cases: list[tuple[str, Path, Path]] = []
    for fixture in sorted(FIXTURE_INVALID.glob("*.json")):
        stem = fixture.stem
        assert "__" in stem, f"invalid fixture must be <record>__<reason>.json: {fixture}"
        record = stem.split("__", 1)[0]
        assert record in MODEL_BY_KIND, f"unknown record kind in fixture: {fixture}"
        expected = fixture.with_suffix(".expected.txt")
        assert expected.exists(), f"missing expected substring for {fixture}: {expected}"
        cases.append((record, fixture, expected))
    return cases


INVALID_CASES = _collect_invalid_fixtures()


def test_invalid_fixture_count_per_record() -> None:
    per_record: dict[str, int] = {}
    for record, _, _ in INVALID_CASES:
        per_record[record] = per_record.get(record, 0) + 1
    for kind in MODEL_BY_KIND:
        assert per_record.get(kind, 0) >= 2, f"need at least two invalid fixtures for {kind}"


@pytest.mark.parametrize(
    "record,fixture,expected", INVALID_CASES,
    ids=[f.stem for _, f, _ in INVALID_CASES],
)
def test_invalid_fixture_is_rejected(record: str, fixture: Path, expected: Path) -> None:
    data = json.loads(fixture.read_text())
    errors = validate(record, data)
    assert errors, f"fixture {fixture.name} should be rejected but validate() returned []"
    needle = expected.read_text().splitlines()[0].strip()
    assert needle, f"expected.txt for {fixture.name} must have a non-empty first line"
    joined = "\n".join(errors)
    assert needle in joined, (
        f"fixture {fixture.name}: expected substring {needle!r} "
        f"not found in validator errors:\n{joined}"
    )


# ---------------------------------------------------------------------------
# can_transition
# ---------------------------------------------------------------------------


def test_documented_transitions_are_allowed() -> None:
    for src, allowed in TRANSITIONS.items():
        for dst in allowed:
            assert can_transition(src, dst), f"{src} -> {dst} must be allowed"


@pytest.mark.parametrize(
    "src,dst",
    [
        (NativeOpState.queued, NativeOpState.succeeded),
        (NativeOpState.queued, NativeOpState.dispatched),
        (NativeOpState.dispatched, NativeOpState.queued),
        (NativeOpState.succeeded, NativeOpState.claimed),
        (NativeOpState.cancelled_before_dispatch, NativeOpState.claimed),
        (NativeOpState.failed, NativeOpState.dispatched),
        (NativeOpState.claimed, NativeOpState.succeeded),
    ],
)
def test_disallowed_transitions(src: NativeOpState, dst: NativeOpState) -> None:
    assert not can_transition(src, dst)


def test_unknown_state_name_rejects() -> None:
    assert not can_transition("invented", NativeOpState.succeeded)
    assert not can_transition(NativeOpState.queued, "invented")


# ---------------------------------------------------------------------------
# AddressRepr round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory,expected",
    [
        (lambda: AddressRepr.module_rel("UnityFramework", 0x12345), "mod:UnityFramework+0x12345"),
        (lambda: AddressRepr.absolute(0x7FFEE0001234), "abs:0x7FFEE0001234"),
        (lambda: AddressRepr.null(), "null"),
        (lambda: AddressRepr.placeholder(), "placeholder"),
        (lambda: AddressRepr.zero_offset(), "+0x0"),
    ],
)
def test_address_round_trip(factory, expected: str) -> None:
    original = factory()
    serialised = original.to_str()
    assert serialised == expected
    parsed = AddressRepr.from_str(serialised)
    assert parsed == original
    assert parsed.to_str() == expected


def test_address_rejects_raw_numeric_input() -> None:
    for bad in (0, 12345, 0x1000, True):
        with pytest.raises(ValueError) as info:
            AddressRepr.from_str(bad)  # type: ignore[arg-type]
        assert "address" in str(info.value).lower(), info.value


def test_address_rejects_unparseable_strings() -> None:
    for bad in ("0x1234", "mod:+0x1", "abs:1234", "", "mod:Name+1", "mod:bad char+0x1"):
        with pytest.raises(ValueError):
            AddressRepr.from_str(bad)


# ---------------------------------------------------------------------------
# CONTRACT_VERSION parity across languages
# ---------------------------------------------------------------------------


def test_python_contract_version() -> None:
    assert CONTRACT_VERSION == 1


def test_typescript_contract_version() -> None:
    ts_path = REPO_ROOT / "src" / "types" / "gameRuntime.ts"
    text = ts_path.read_text()
    # The export must sit next to the number 1.
    assert "export const CONTRACT_VERSION = 1" in text, (
        "TypeScript binding must export CONTRACT_VERSION = 1"
    )


def test_rust_contract_version() -> None:
    rs_path = REPO_ROOT / "src-tauri" / "src" / "game_runtime_types.rs"
    text = rs_path.read_text()
    # Tolerate whitespace variations in the function body.
    assert "fn contract_version()" in text
    # The literal 1 must appear in the function body returning u32.
    snippet = text.split("fn contract_version()", 1)[1].split("}", 1)[0]
    assert "1" in snippet, "Rust contract_version() must return 1"


# ---------------------------------------------------------------------------
# JSON Schema parsing + field parity with the Pydantic models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,filename", sorted(SCHEMA_FILES.items()))
def test_schema_file_parses_and_has_headers(kind: str, filename: str) -> None:
    schema_path = CONTRACTS_DIR / filename
    schema = json.loads(schema_path.read_text())
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert "$id" in schema, f"{filename} must declare an $id"
    assert schema.get("type") == "object"


@pytest.mark.parametrize("kind,filename", sorted(SCHEMA_FILES.items()))
def test_schema_keys_match_pydantic_model(kind: str, filename: str) -> None:
    schema = json.loads((CONTRACTS_DIR / filename).read_text())
    model = MODEL_BY_KIND[kind]
    model_fields = set(model.model_fields.keys())
    schema_props = set(schema.get("properties", {}).keys())
    schema_required = set(schema.get("required", []))
    # Property keys and required keys union must equal the model's fields.
    assert (schema_props | schema_required) == model_fields, (
        f"{filename}: schema properties/required {(schema_props | schema_required)} "
        f"must equal Pydantic fields {model_fields}"
    )
    # Every required key must also appear in properties.
    assert schema_required <= schema_props, (
        f"{filename}: required set {schema_required - schema_props} missing from properties"
    )
