"""Synthetic reviewer-authored benchmark fixture bank for P01 retrieval.

This is a deterministic engineering fixture, not the operator's uploaded
course. Passing it does not establish real-course retrieval coverage.

Each case is a data-only record. ``expected_passage_hint`` is a reviewer-
authored substring that MUST appear in a top-5 passage for an answerable
case. For negative cases the expected behaviour is a gap/qualification;
``expected_passage_hint`` is left empty.

The lesson corpus is enumerated here as well. Tests import the corpus
verbatim into the knowledge store, run retrieval and check that the hint
substring is present without letting the retriever choose its own hint.
"""
from __future__ import annotations

from dataclasses import dataclass, field


CASE_TYPES = frozenset({
    "answerable",
    "negative-missing",
    "negative-platform-conflict",
    "correction-expected",
})


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    query: str
    expected_passage_hint: str
    case_type: str
    platform_scope: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.case_type not in CASE_TYPES:
            raise ValueError(f"unknown case_type: {self.case_type!r}")
        if self.case_type == "answerable" and not self.expected_passage_hint:
            raise ValueError(
                f"answerable case {self.id!r} must have a reviewer hint"
            )


@dataclass(frozen=True)
class CorpusLesson:
    source_id: str
    title: str
    platform: str
    text: str


# ---------------------------------------------------------------------------
# Lesson corpus (reviewer-authored)
# ---------------------------------------------------------------------------
# Each lesson's filename intentionally avoids the operation keyword in the
# query, so a title-matching retriever would miss it. The lesson body
# contains the reviewer hint.

CORPUS: tuple[CorpusLesson, ...] = (
    CorpusLesson(
        "lesson_01_intro", "Lesson A", "shared",
        "The investigation starts by observing the baseline cooldown "
        "before any changes are written to the local player's kill timer.",
    ),
    CorpusLesson(
        "lesson_02_scanner", "Lesson B", "shared",
        "A changed-value scan narrows a floating counter by taking a "
        "first snapshot and then refining after each observed decrement.",
    ),
    CorpusLesson(
        "lesson_03_pointer", "Lesson C", "shared",
        "Pointer chains resolve from a module-relative root through "
        "successive dereferences; record the walked offsets as a recipe.",
    ),
    CorpusLesson(
        "lesson_04_entity", "Lesson D", "shared",
        "The entity list is reached from the camera root via a cached "
        "pointer; iterate entries until the local player's slot is found.",
    ),
    CorpusLesson(
        "lesson_05_cooldown_mac", "Lesson E", "macos",
        "On macOS the cooldown field is written through the Unity "
        "scripting backend; the controlling field is called killTimer.",
    ),
    CorpusLesson(
        "lesson_06_cooldown_win", "Lesson F", "windows",
        "On Windows the same cooldown is reachable via ReadProcessMemory "
        "against the pinned module base plus the killTimer offset.",
    ),
    CorpusLesson(
        "lesson_07_readback", "Lesson G", "shared",
        "A write is only considered applied after a readback of the new "
        "value and a separate check that the game accepted the action.",
    ),
    CorpusLesson(
        "lesson_08_cleanup", "Lesson H", "shared",
        "Cleanup never restores a stale captured timer; a countdown "
        "returns to normal by ending the override and waiting for a round.",
    ),
    CorpusLesson(
        "lesson_09_scan_boundaries", "Lesson I", "shared",
        "Scans respect region boundaries: never report a partial search "
        "as exhaustive; record coverage and the regions actually skipped.",
    ),
    CorpusLesson(
        "lesson_10_restart", "Lesson J", "shared",
        "Restart verification re-resolves the saved recipe against three "
        "fresh processes before a mapping is marked restart_verified.",
    ),
    CorpusLesson(
        "lesson_11_stale_recipe", "Lesson K", "shared",
        "A stale recipe is detected when its target fingerprint no longer "
        "matches the running build; refuse to execute the old steps.",
    ),
    CorpusLesson(
        "lesson_12_overlay", "Lesson L", "shared",
        "Overlay boxes disappear after 500 ms without a valid frame; "
        "nullable fields such as team must not be filled with guesses.",
    ),
    CorpusLesson(
        "lesson_13_frame_identity", "Lesson M", "shared",
        "Each frame carries schema, session, process and build identity "
        "so the receiver can drop mismatched frames before rendering.",
    ),
    CorpusLesson(
        "lesson_14_stop", "Lesson N", "shared",
        "A Stop clears active work in two seconds; cleanup may run for "
        "another two seconds before the helper is terminated.",
    ),
    CorpusLesson(
        "lesson_15_write_ack", "Lesson O", "shared",
        "Writes require an acknowledgement from the helper and are "
        "marked outcome_unknown when the response is lost in transit.",
    ),
    CorpusLesson(
        "lesson_16_identity_bind", "Lesson P", "shared",
        "Sessions bind to the process birth identity, never to a bare "
        "PID; a reused PID is refused without a new identity receipt.",
    ),
    CorpusLesson(
        "lesson_17_scan_types", "Lesson Q", "shared",
        "Unknown-initial scans are supported alongside changed and "
        "unchanged refinement; the typed-value mode records the width.",
    ),
    CorpusLesson(
        "lesson_18_android_launch", "Lesson R", "android",
        "The Android lane launches an instrumented test package before "
        "any in-process reader attaches; rooted access does not qualify.",
    ),
    CorpusLesson(
        "lesson_19_android_sandbox", "Lesson S", "android",
        "The Android sandbox blocks cross-package memory reads; a second "
        "non-root app cannot read the game's memory without instrumentation.",
    ),
    CorpusLesson(
        "lesson_20_feature_lifecycle", "Lesson T", "shared",
        "Feature lifecycle: start, verify, monitor, stop; writes are "
        "revoked when the local player identity is lost.",
    ),
    CorpusLesson(
        "lesson_21_corrections", "Lesson U", "shared",
        "An operator correction supersedes the quoted passage; later "
        "retrievals cite the correction with provenance and operator id.",
    ),
    CorpusLesson(
        "lesson_22_ac_vectors", "Lesson V", "shared",
        "Anticheat detection vectors include signed-driver checks, "
        "module-walk integrity and kernel-mode handle enumeration.",
    ),
    CorpusLesson(
        "lesson_23_bypass_strategy", "Lesson W", "shared",
        "Mapper strategies hide allocation metadata by manual mapping "
        "and refuse to touch cross-process handles the driver watches.",
    ),
    CorpusLesson(
        "lesson_24_syscalls", "Lesson X", "shared",
        "Indirect syscalls route through resolved SSNs and defeat "
        "user-mode hooks that user-land ETW patching also removes.",
    ),
    CorpusLesson(
        "lesson_25_evasion", "Lesson Y", "shared",
        "Sleep obfuscation unlinks the thread from the usual kernel "
        "stacks; a scanner cannot wake it with a naked SuspendThread.",
    ),
    CorpusLesson(
        "lesson_26_evidence", "Lesson Z", "shared",
        "Every experiment records question, inputs, observations and "
        "artifacts; a screenshot alone does not establish feature success.",
    ),
    CorpusLesson(
        "lesson_27_import", "Lesson AA", "shared",
        "Importing a mapping preserves provenance and cannot elevate its "
        "verification status: a candidate stays candidate until re-run.",
    ),
    CorpusLesson(
        "lesson_28_export", "Lesson AB", "shared",
        "Exported examples carry redacted citations only; private paths "
        "and device identifiers never leave the operator's workbench.",
    ),
    CorpusLesson(
        "lesson_29_native_helper", "Lesson AC", "shared",
        "The native helper owns a single executor per target process and "
        "serialises competing attach requests through that one owner.",
    ),
    CorpusLesson(
        "lesson_30_window_tracking", "Lesson AD", "shared",
        "Overlay follows the game window via platform events; a DPI "
        "change triggers a reprojection before the next frame is drawn.",
    ),
)


# ---------------------------------------------------------------------------
# Cases (30 answerable, 10 negatives/corrections)
# ---------------------------------------------------------------------------

ANSWERABLE_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("A01", "observe the baseline before removing cooldown",
                  "observing the baseline cooldown", "answerable"),
    BenchmarkCase("A02", "narrow a floating counter with changed value",
                  "changed-value scan", "answerable"),
    BenchmarkCase("A03", "walk a pointer chain to a value",
                  "Pointer chains resolve", "answerable"),
    BenchmarkCase("A04", "find the entity list from the camera",
                  "entity list is reached from the camera", "answerable"),
    BenchmarkCase("A05", "controlling field for kill timer on mac",
                  "killTimer", "answerable", platform_scope="macos"),
    BenchmarkCase("A06", "write cooldown on windows via native read memory",
                  "ReadProcessMemory", "answerable", platform_scope="windows"),
    BenchmarkCase("A07", "confirm a write was applied",
                  "readback", "answerable"),
    BenchmarkCase("A08", "safely restore normal cooldown after disabling",
                  "stale captured timer", "answerable"),
    BenchmarkCase("A09", "scan region boundaries and partial coverage",
                  "partial search as exhaustive", "answerable"),
    BenchmarkCase("A10", "verify a saved mapping across fresh processes",
                  "restart_verified", "answerable"),
    BenchmarkCase("A11", "detect a stale recipe after a game update",
                  "target fingerprint no longer", "answerable"),
    BenchmarkCase("A12", "overlay disappears when frames stop",
                  "500 ms without a valid frame", "answerable"),
    BenchmarkCase("A13", "frame identity fields and mismatch handling",
                  "schema, session, process and build identity", "answerable"),
    BenchmarkCase("A14", "stop clears active work and cleanup budget",
                  "Stop clears active work in two seconds", "answerable"),
    BenchmarkCase("A15", "treat a lost write response correctly",
                  "outcome_unknown", "answerable"),
    BenchmarkCase("A16", "bind a session to process identity not pid",
                  "process birth identity", "answerable"),
    BenchmarkCase("A17", "support unknown initial value scans",
                  "Unknown-initial scans", "answerable"),
    BenchmarkCase("A18", "launch an instrumented test package on android",
                  "instrumented test package", "answerable",
                  platform_scope="android"),
    BenchmarkCase("A19", "cross-package memory reads on android sandbox",
                  "Android sandbox blocks cross-package", "answerable",
                  platform_scope="android"),
    BenchmarkCase("A20", "feature lifecycle states and stop semantics",
                  "start, verify, monitor, stop", "answerable"),
    BenchmarkCase("A21", "operator correction supersedes imported text",
                  "correction supersedes the quoted passage", "answerable"),
    BenchmarkCase("A22", "anticheat detection vectors to watch for",
                  "signed-driver checks", "answerable"),
    BenchmarkCase("A23", "mapper strategies that hide allocation metadata",
                  "manual mapping", "answerable"),
    BenchmarkCase("A24", "defeat user-mode hooks with indirect syscalls",
                  "Indirect syscalls", "answerable"),
    BenchmarkCase("A25", "hide a thread while it sleeps",
                  "Sleep obfuscation", "answerable"),
    BenchmarkCase("A26", "what counts as feature success evidence",
                  "screenshot alone", "answerable"),
    BenchmarkCase("A27", "importing a mapping and its verification status",
                  "candidate stays candidate", "answerable"),
    BenchmarkCase("A28", "what redaction applies to exported examples",
                  "redacted citations", "answerable"),
    BenchmarkCase("A29", "native helper ownership for one target process",
                  "single executor per target process", "answerable"),
    BenchmarkCase("A30", "track the game window across dpi changes",
                  "DPI change triggers a reprojection", "answerable"),
)


NEGATIVE_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("N01", "how to tune quantum tunneling resistance",
                  "", "negative-missing",
                  notes="topic absent from corpus"),
    BenchmarkCase("N02", "procedure for brewing espresso with steam wand",
                  "", "negative-missing",
                  notes="topic absent from corpus"),
    BenchmarkCase("N03", "guide to tax filing deadlines for freelancers",
                  "", "negative-missing",
                  notes="topic absent from corpus"),
    BenchmarkCase("N04", "how to bake a sourdough loaf at altitude",
                  "", "negative-missing",
                  notes="topic absent from corpus"),
    BenchmarkCase("N05", "windows killTimer offset used on macos lane",
                  "", "negative-platform-conflict",
                  platform_scope="macos",
                  notes="query asks for the macos passage but names the "
                        "windows field; lane must be flagged"),
    BenchmarkCase("N06", "macos ReadProcessMemory usage on android device",
                  "", "negative-platform-conflict",
                  platform_scope="android",
                  notes="platforms conflict; expect a qualification"),
    BenchmarkCase("N07", "run the mac killTimer write on an android build",
                  "", "negative-platform-conflict",
                  platform_scope="android",
                  notes="platform conflict"),
    BenchmarkCase("N08", "use the baseline procedure before an operator fix",
                  "", "correction-expected",
                  notes="before a correction is applied this query returns "
                        "the baseline; after the correction, the corrected "
                        "text must be used"),
    BenchmarkCase("N09", "use the corrected cleanup instead of the old one",
                  "", "correction-expected",
                  notes="expects corrected procedure and exclusion of the "
                        "superseded passage"),
    BenchmarkCase("N10", "re-verify a mapping after the operator's fix",
                  "", "correction-expected",
                  notes="procedure must be marked stale after correction"),
)


ALL_CASES: tuple[BenchmarkCase, ...] = ANSWERABLE_CASES + NEGATIVE_CASES


def corpus() -> tuple[CorpusLesson, ...]:
    """Return the reviewer-authored lesson corpus."""
    return CORPUS


def answerable_cases() -> tuple[BenchmarkCase, ...]:
    return ANSWERABLE_CASES


def negative_cases() -> tuple[BenchmarkCase, ...]:
    return NEGATIVE_CASES


def all_cases() -> tuple[BenchmarkCase, ...]:
    return ALL_CASES


__all__ = [
    "BenchmarkCase",
    "CorpusLesson",
    "CASE_TYPES",
    "CORPUS",
    "ANSWERABLE_CASES",
    "NEGATIVE_CASES",
    "ALL_CASES",
    "corpus",
    "answerable_cases",
    "negative_cases",
    "all_cases",
]
