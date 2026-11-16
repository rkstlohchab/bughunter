"""Game runtime package: shared wire contracts, services and helpers.

This package is introduced at phase P00. Only the ``contracts`` module is
populated so far; later phases add ``service``, ``broker``, ``targets``,
``knowledge``, ``mappings``, ``features`` and ``experiments``.
"""
from __future__ import annotations

from .contracts import (
    ADDRESS_PATTERN,
    CONTRACT_VERSION,
    AddressRepr,
    BuildFingerprint,
    CheckResult,
    ErrorCode,
    Evidence,
    FeatureRun,
    GameSession,
    GameTarget,
    ImplStatus,
    MappingRecipe,
    Module,
    NativeOperation,
    NativeOpState,
    PointerStep,
    TRANSITIONS,
    TargetStatus,
    can_transition,
    validate,
    validate_record,
)

__all__ = [
    "CONTRACT_VERSION",
    "ADDRESS_PATTERN",
    "AddressRepr",
    "BuildFingerprint",
    "CheckResult",
    "ErrorCode",
    "Evidence",
    "FeatureRun",
    "GameSession",
    "GameTarget",
    "ImplStatus",
    "MappingRecipe",
    "Module",
    "NativeOperation",
    "NativeOpState",
    "PointerStep",
    "TRANSITIONS",
    "TargetStatus",
    "can_transition",
    "validate",
    "validate_record",
]
