"""Shared wire contracts for the game runtime.

Authoritative Python models that mirror the JSON Schema files in
``contracts/game_runtime/`` and the TypeScript/Rust bindings. This module is
deliberately logic-light: it exports strict Pydantic models, a stable set of
enum values, a tiny ``AddressRepr`` helper for address strings, and a
validator that produces human-readable error strings for fixture-based
parity checks.

Cross-language parity rules (enforced here):

- Every field a JSON Schema marks as required is also required on the
  Pydantic model (no default, no ``default_factory``). Callers must supply
  every wire field explicitly, including nullable ones such as
  ``last_error`` or ``executor_epoch``.
- Every record runs with ``ConfigDict(strict=True, extra='forbid')``. This
  blocks the five mismatches Codex reproduced against the prior loose model:
  ``revision=0`` (fails ``ge=1``), ``revision=True`` (strict mode refuses the
  bool-as-int coercion), ``revision="2"`` (strict mode refuses the
  string-as-int coercion), ``schema_version=-1`` (fails ``ge=1``) and a
  ``GameTarget`` missing ``modules`` / ``capabilities`` (the arrays are now
  required with no implicit ``[]`` default).
- Integer fields with wire bounds use explicit ``Field(ge=...)`` validators
  that match the JSON Schema ``minimum``.
- Addresses, module RVAs and signed pointer offsets are serialised as
  strings. JavaScript ``Number`` cannot safely hold 64-bit values.
- Address strings normalise through :class:`AddressRepr` to one of five
  forms::

    mod:<module>+0x<hex>     module-relative
    abs:0x<hex>              absolute (process-bound)
    null                     missing / unresolved
    placeholder              deliberate placeholder (not a value)
    +0x0                     legitimate zero offset (distinct from null)

Schema evolution: bump :data:`CONTRACT_VERSION` for breaking wire changes and
``MappingRecipe.schema_version`` for recipe-local layout bumps.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


CONTRACT_VERSION: int = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ErrorCode(StrEnum):
    invalid_input = "invalid_input"
    busy = "busy"
    outcome_unknown = "outcome_unknown"
    stale_recipe = "stale_recipe"
    target_unavailable = "target_unavailable"
    executor_epoch_mismatch = "executor_epoch_mismatch"
    revision_conflict = "revision_conflict"
    precondition_failed = "precondition_failed"
    cleanup_timeout = "cleanup_timeout"
    helper_exited = "helper_exited"
    unsupported_build = "unsupported_build"
    permission_denied = "permission_denied"


class NativeOpState(StrEnum):
    queued = "queued"
    claimed = "claimed"
    dispatched = "dispatched"
    succeeded = "succeeded"
    failed = "failed"
    outcome_unknown = "outcome_unknown"
    cancelled_before_dispatch = "cancelled_before_dispatch"
    cancellation_requested = "cancellation_requested"


class ImplStatus(StrEnum):
    not_started = "not_started"
    working = "working"
    ready_for_review = "ready_for_review"
    verified = "verified"
    failed = "failed"
    blocked = "blocked"


class TargetStatus(StrEnum):
    not_run = "not_run"
    static_only = "static_only"
    runtime_access_blocked = "runtime_access_blocked"
    runtime_ready = "runtime_ready"
    feature_verified = "feature_verified"
    stale = "stale"


class CheckResult(StrEnum):
    pass_ = "pass"  # ``pass`` is a reserved word; the wire value remains "pass"
    fail = "fail"
    blocked = "blocked"
    not_run = "not_run"


# ---------------------------------------------------------------------------
# Native operation state machine
# ---------------------------------------------------------------------------


TRANSITIONS: dict[NativeOpState, frozenset[NativeOpState]] = {
    NativeOpState.queued: frozenset({
        NativeOpState.claimed,
        NativeOpState.cancelled_before_dispatch,
    }),
    NativeOpState.claimed: frozenset({
        NativeOpState.dispatched,
        NativeOpState.cancelled_before_dispatch,
    }),
    NativeOpState.dispatched: frozenset({
        NativeOpState.succeeded,
        NativeOpState.failed,
        NativeOpState.outcome_unknown,
        NativeOpState.cancellation_requested,
    }),
    NativeOpState.cancellation_requested: frozenset({
        NativeOpState.succeeded,
        NativeOpState.failed,
        NativeOpState.outcome_unknown,
    }),
    NativeOpState.succeeded: frozenset(),
    NativeOpState.failed: frozenset(),
    NativeOpState.outcome_unknown: frozenset(),
    NativeOpState.cancelled_before_dispatch: frozenset(),
}


def can_transition(src: NativeOpState | str, dst: NativeOpState | str) -> bool:
    """True iff ``src -> dst`` is a documented native-operation transition."""
    try:
        src_s = NativeOpState(src)
        dst_s = NativeOpState(dst)
    except ValueError:
        return False
    return dst_s in TRANSITIONS.get(src_s, frozenset())


# ---------------------------------------------------------------------------
# AddressRepr
# ---------------------------------------------------------------------------


_MODULE_RE = re.compile(r"^mod:([A-Za-z0-9._-]+)\+0x([0-9A-Fa-f]+)$")
_ABS_RE = re.compile(r"^abs:0x([0-9A-Fa-f]+)$")
_ZERO_RE = re.compile(r"^\+0x0$")


class AddressRepr:
    """Normalised address string with explicit null/placeholder/zero cases.

    Internal numeric values are Python ``int``; the wire form is always one
    of the five documented strings. Raw numeric input from JSON is refused:
    callers must serialise through ``AddressRepr`` so 64-bit values survive
    JavaScript's ``Number``.
    """

    __slots__ = ("kind", "module", "value")

    _Kind = Literal["module_rel", "absolute", "null", "placeholder", "zero_offset"]

    def __init__(self, kind: str, module: str | None = None, value: int | None = None) -> None:
        self.kind: str = kind
        self.module: str | None = module
        self.value: int | None = value

    # Factories ------------------------------------------------------------
    @classmethod
    def module_rel(cls, name: str, rva: int) -> "AddressRepr":
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError(f"invalid module name: {name!r}")
        if not isinstance(rva, int) or isinstance(rva, bool) or rva < 0:
            raise ValueError(f"invalid rva: {rva!r}")
        return cls("module_rel", module=name, value=rva)

    @classmethod
    def absolute(cls, addr: int) -> "AddressRepr":
        if not isinstance(addr, int) or isinstance(addr, bool) or addr < 0:
            raise ValueError(f"invalid absolute address: {addr!r}")
        return cls("absolute", value=addr)

    @classmethod
    def null(cls) -> "AddressRepr":
        return cls("null")

    @classmethod
    def placeholder(cls) -> "AddressRepr":
        return cls("placeholder")

    @classmethod
    def zero_offset(cls) -> "AddressRepr":
        return cls("zero_offset", value=0)

    # Parsing / serialising -----------------------------------------------
    @classmethod
    def from_str(cls, text: Any) -> "AddressRepr":
        if isinstance(text, bool) or not isinstance(text, str):
            raise ValueError(
                "address must be a string in one of the documented forms "
                "(mod:/abs:/null/placeholder/+0x0); numeric JSON values are refused"
            )
        if text == "null":
            return cls.null()
        if text == "placeholder":
            return cls.placeholder()
        if _ZERO_RE.match(text):
            return cls.zero_offset()
        match = _MODULE_RE.match(text)
        if match:
            name, hex_rva = match.group(1), match.group(2)
            return cls.module_rel(name, int(hex_rva, 16))
        match = _ABS_RE.match(text)
        if match:
            return cls.absolute(int(match.group(1), 16))
        raise ValueError(f"unparseable address: {text!r}")

    def to_str(self) -> str:
        if self.kind == "null":
            return "null"
        if self.kind == "placeholder":
            return "placeholder"
        if self.kind == "zero_offset":
            return "+0x0"
        if self.kind == "module_rel":
            assert self.module is not None and self.value is not None
            return f"mod:{self.module}+0x{self.value:X}"
        if self.kind == "absolute":
            assert self.value is not None
            return f"abs:0x{self.value:X}"
        raise ValueError(f"unknown address kind: {self.kind}")

    # Dunder ---------------------------------------------------------------
    def __str__(self) -> str:
        return self.to_str()

    def __repr__(self) -> str:
        return f"AddressRepr({self.to_str()!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AddressRepr):
            return self.to_str() == other.to_str()
        if isinstance(other, str):
            try:
                return self.to_str() == AddressRepr.from_str(other).to_str()
            except ValueError:
                return False
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.to_str())


ADDRESS_PATTERN = r"^(mod:[A-Za-z0-9._-]+\+0x[0-9A-Fa-f]+|abs:0x[0-9A-Fa-f]+|null|placeholder|\+0x0)$"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIZE_HEX_RE = re.compile(r"^0x[0-9A-Fa-f]+$")
_SIGNED_HEX_RE = re.compile(r"^[+-]?0x[0-9A-Fa-f]+$")


# ---------------------------------------------------------------------------
# Shared Pydantic base
# ---------------------------------------------------------------------------


class _Record(BaseModel):
    """Strict base: unknown fields refused, no implicit coercion.

    ``strict=True`` is the lever that fixes four of the five parity mismatches
    Codex reproduced: Pydantic stops coercing booleans and string digits into
    integers, so ``revision=True`` and ``revision="2"`` fail at parse time.
    """

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=False,
    )


def _validate_address_string(value: Any) -> str:
    """Pydantic field validator reused by every address-bearing field."""
    if isinstance(value, AddressRepr):
        return value.to_str()
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(
            "address fields must be strings (mod:/abs:/null/placeholder/+0x0); "
            "64-bit numeric values are strings on the wire"
        )
    # Round-trip through AddressRepr to normalise and verify.
    return AddressRepr.from_str(value).to_str()


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------


class BuildFingerprint(_Record):
    sha256_main: str
    sha256_metadata: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256_main")
    @classmethod
    def _main_hex(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256_main must be a 64-character lowercase hex digest")
        return value

    @field_validator("sha256_metadata")
    @classmethod
    def _meta_hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256_metadata must be a 64-character lowercase hex digest")
        return value


class Module(_Record):
    name: str
    base_rva: str
    size: str
    sha256: str

    _address_fields: ClassVar[tuple[str, ...]] = ("base_rva",)

    @field_validator("base_rva")
    @classmethod
    def _base_rva_is_address(cls, value: Any) -> str:
        return _validate_address_string(value)

    @field_validator("size")
    @classmethod
    def _size_is_hex_str(cls, value: Any) -> str:
        if not isinstance(value, str) or not _SIZE_HEX_RE.fullmatch(value):
            raise ValueError("size must be a hex string like '0x1234' (64-bit values are strings)")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("module sha256 must be a 64-character lowercase hex digest")
        return value


class PointerStep(_Record):
    op: Literal["add", "deref"]
    offset: str
    width: Literal[4, 8]

    @field_validator("offset")
    @classmethod
    def _offset_hex(cls, value: Any) -> str:
        if not isinstance(value, str) or not _SIGNED_HEX_RE.fullmatch(value):
            raise ValueError("pointer step offset must be a signed hex string, e.g. '+0x18' or '-0x8'")
        return value


class Evidence(_Record):
    id: str
    kind: Literal["screenshot", "video", "log", "memory_capture", "model_note", "report", "other"]
    content_type: str
    path: str | None = None
    sha256: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Top-level records. Every field a schema marks required is required here
# too: no ``default_factory``, no implicit ``None`` default for nullables.
# ---------------------------------------------------------------------------


class GameTarget(_Record):
    id: str
    game_id: str
    platform: Literal["macos", "windows", "android"]
    runtime: str
    architecture: str
    pointer_width: Literal[4, 8]
    executable_or_package: str
    version: str
    build_fingerprint: BuildFingerprint
    modules: list[Module]
    capabilities: list[str]


class GameSession(_Record):
    id: str
    target_id: str
    harness_session_id: str
    session_generation: int = Field(ge=0)
    stop_generation: int = Field(ge=0)
    process_instance_id: str
    knowledge_collection_ids: list[str]
    state: Literal["idle", "starting", "running", "stopping", "stopped", "failed", "interrupted"]
    last_error: str | None


class MappingRecipe(_Record):
    id: str
    schema_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    target_fingerprint: str
    name: str
    value_type: Literal[
        "u8", "u16", "u32", "u64",
        "i8", "i16", "i32", "i64",
        "f32", "f64", "bytes",
    ]
    root: str
    steps: list[PointerStep]
    preconditions: list[str]
    evidence_ids: list[str]
    verification_status: Literal["candidate", "observed", "restart_verified", "stale"]

    @field_validator("root")
    @classmethod
    def _root_is_address(cls, value: Any) -> str:
        return _validate_address_string(value)


class NativeOperation(_Record):
    id: str
    request_hash: str
    session_id: str
    session_generation: int = Field(ge=0)
    stop_generation: int = Field(ge=0)
    process_instance_id: str
    target_fingerprint: str
    kind: Literal["read", "write", "scan", "pointer_search", "tool_control"]
    effect_class: Literal["read_only", "read_write", "mutating"]
    arguments: dict[str, Any]
    preconditions: list[str]
    status: Literal[
        "queued", "claimed", "dispatched", "succeeded", "failed",
        "outcome_unknown", "cancelled_before_dispatch", "cancellation_requested",
    ]
    revision: int = Field(ge=0)
    executor_id: str | None
    executor_epoch: int | None
    lease_expires_at: str | None
    attempt_id: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    evidence_ids: list[str]

    @field_validator("request_hash")
    @classmethod
    def _request_hash_nonempty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("request_hash must be a non-empty string; completion receipts cannot fabricate one")
        return value

    @field_validator("executor_epoch")
    @classmethod
    def _executor_epoch_nonneg(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("executor_epoch must be a non-negative integer or null")
        return value


class FeatureRun(_Record):
    id: str
    session_id: str
    feature_id: str
    recipe_revisions: list[int]
    configuration: dict[str, Any]
    state: Literal[
        "requested", "starting", "running", "verifying", "monitoring",
        "stopping", "stopped", "failed", "needs_input", "blocked",
    ]
    readback_status: Literal["pending", "pass", "fail", "not_applicable"]
    behavior_status: Literal["pending", "pass", "fail", "not_applicable"]
    cleanup_status: Literal["pending", "pass", "fail", "not_applicable"]
    evidence_ids: list[str]

    @field_validator("recipe_revisions")
    @classmethod
    def _revisions_positive(cls, value: list[int]) -> list[int]:
        for i, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"recipe_revisions[{i}] must be an integer; booleans/strings are not integers on the wire")
            if item < 1:
                raise ValueError(f"recipe_revisions[{i}] must be a positive integer (>= 1); {item} is not a valid wire value")
        return value


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


_MODEL_BY_KIND: dict[str, type[_Record]] = {
    "game_target": GameTarget,
    "game_session": GameSession,
    "mapping_recipe": MappingRecipe,
    "native_operation": NativeOperation,
    "feature_run": FeatureRun,
}


def _format_validation_errors(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", ()))
        msg = item.get("msg", "invalid value")
        messages.append(f"{loc}: {msg}" if loc else msg)
    return messages


def validate(kind: str, data: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors; empty list means valid.

    ``kind`` is the snake_case record name: ``game_target``, ``game_session``,
    ``mapping_recipe``, ``native_operation`` or ``feature_run``.
    """
    model = _MODEL_BY_KIND.get(kind)
    if model is None:
        return [f"unknown record kind: {kind!r}"]
    if not isinstance(data, dict):
        return [f"{kind}: payload must be a JSON object"]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return _format_validation_errors(exc)
    return []


def validate_record(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate ``data`` against the ``kind`` wire contract, raising on failure.

    Returns the input dict unchanged on success so callers can chain
    ``emit(validate_record("game_target", target.model_dump()))``. The
    docstring contract: every record emitted by game-runtime code goes
    through this function before it reaches another layer, so
    out-of-band mutations cannot slip past strict validation.
    """
    errors = validate(kind, data)
    if errors:
        raise ValueError(
            f"{kind} failed wire validation: " + "; ".join(errors)
        )
    return data


def record_kinds() -> tuple[str, ...]:
    """Public stable list of wire record kinds."""
    return tuple(sorted(_MODEL_BY_KIND))


__all__ = [
    "CONTRACT_VERSION",
    "ADDRESS_PATTERN",
    "AddressRepr",
    "BuildFingerprint",
    "Module",
    "PointerStep",
    "Evidence",
    "GameTarget",
    "GameSession",
    "MappingRecipe",
    "NativeOperation",
    "FeatureRun",
    "ErrorCode",
    "NativeOpState",
    "ImplStatus",
    "TargetStatus",
    "CheckResult",
    "TRANSITIONS",
    "can_transition",
    "validate",
    "validate_record",
    "record_kinds",
]
