"""Read-only taxonomy of user-supplied AI prompt-testing archives.

Archive content is untrusted data. Files are not extracted or executed here;
metadata and bounded excerpts are used for review and retrieval only.
"""
from __future__ import annotations

import re
import hashlib
from collections import Counter

from .security import redact_text

# Explanatory labels, not payloads or instructions for the reasoning model.
CATEGORIES = {
    "Role and persona framing": r"\b(role.?play|persona|pretend to be|act as|character)\b",
    "Authority and instruction spoofing": r"\b(system prompt|developer message|override|ignore previous|higher priority|authority)\b",
    "Multi-turn conditioning": r"\b(multi.?turn|conversation|follow.?up|continue|previous answer)\b",
    "Encoding and obfuscation": r"\b(base64|rot13|hex.?encod|unicode|cipher|obfuscat|encoded)\b",
    "Indirect and retrieved-content injection": r"\b(prompt injection|retriev|tool output|webpage|document|readme|search result)\b",
    "Output-format manipulation": r"\b(json|xml|markdown|format|schema|table|delimiter)\b",
    "Multilingual variation": r"\b(translate|translation|multilingual|language switch|non.?english)\b",
    "Safety-evaluation framing": r"\b(benchmark|evaluation|red.?team|jailbreak|refusal|policy|safety test)\b",
    "Tool and action coercion": r"\b(function call|tool.?call|execute|shell|terminal|browser tool|command)\b",
}
_COMPILED = {name: re.compile(pattern, re.I) for name, pattern in CATEGORIES.items()}
MAX_ANALYZED_CHUNKS = 1500
MAX_EXCERPT = 220


def analyze_source(source: dict) -> dict:
    """Classify a processed library ZIP by existing cited chunks; never run prompts.

    Results are *candidate* labels from literal matches, not a verified outcome,
    efficacy measurement, or a claim that the text is an attack.
    """
    if not str(source.get("filename", "")).lower().endswith(".zip"):
        raise ValueError("Select a ZIP archive from the Knowledge Library.")
    chunks = source.get("chunks") or []
    if not chunks:
        return {
            "source_id": source["id"], "status": source.get("status", "queued"),
            "ready": False, "categories": [], "examples": [],
            "message": "The ZIP has no indexed text yet. Review its import status and retry.",
        }
    counts = Counter()
    seen = set()
    duplicate_passages = 0
    unclassified_passages = 0
    examples = []
    for chunk in chunks[:MAX_ANALYZED_CHUNKS]:
        content = str(chunk.get("text") or "")
        fingerprint = hashlib.sha256(content.strip().encode('utf-8')).digest()
        if fingerprint in seen:
            duplicate_passages += 1
        seen.add(fingerprint)
        matches = [name for name, pattern in _COMPILED.items() if pattern.search(content)]
        counts.update(matches)
        if not matches:
            unclassified_passages += 1
        if matches and len(examples) < 30:
            examples.append({
                "chunk_id": chunk.get("id"),
                "locator": str(chunk.get("locator") or "Document")[:240],
                "categories": matches,
                "excerpt": redact_text(content[:MAX_EXCERPT]),
            })
    return {
        "source_id": source["id"], "status": source.get("status"),
        "ready": True,
        "analyzed_chunks": min(len(chunks), MAX_ANALYZED_CHUNKS),
        "total_chunks": len(chunks),
        "duplicate_passages": duplicate_passages,
        "unclassified_passages": unclassified_passages,
        "categories": [{"name": name, "matching_passages": counts[name]}
                       for name in CATEGORIES if counts[name]],
        "examples": examples,
        "message": "Candidate categories only; source contents are untrusted. Review before adding training labels. No prompts were executed.",
    }

# Bundled, high-level reference. Kept separate from uploaded evidence, since a
# research taxonomy is not proof that any specific technique works.
CURATED_GUIDE = '''# AI model robustness and prompt-injection research map

This is a descriptive taxonomy, not a collection of executable prompts. It
supports categorizing supplied experiments and designing controlled evaluations.
No category implies a successful bypass. Compare with held-out test cases.

## Instruction hierarchy and claimed authority
Tests how a model handles user content pretending to be higher-priority system
or developer instructions. Measure whether authentic instructions remain in force.

## Role and persona framing
Tests whether adopting a fictional or professional role alters response quality,
truthfulness, or adherence to the configured behavior.

## Multi-turn conditioning
Studies whether instructions retain their priority across long conversations,
context changes and follow-up questions.

## Long context and saturation
Measures whether instructions, constraints, and important source references are
lost in large input windows or many repeated messages.

## Encoding, obfuscation and language variation
Compares responses to encoded, transformed, transliterated and multilingual
representations of otherwise equivalent requests.

## Format and structured-output pressure
Compares formats such as JSON, markdown, XML, tables and strict-schema requests,
including failures to return valid machine-readable outputs.

## Quoted-content and document injection
Measures whether untrusted documents, PDFs, repository files and web pages get
mistaken for direct instructions. Preserve citations and the original source.

## Retrieval and tool-output laundering
Tests how an agent responds when tool responses, search snippets or a retrieval
index contain misleading claims or instructions. Record provenance separately.

## System-prompt extraction pressure
Evaluates whether private instructions or tokens leak in responses. Store any
real leaked material only in encrypted/restricted evidence; redact exports.

## Tool-action coercion
Measures whether a model attempts unauthorized commands or side effects based
on lower-trust text. Evaluate actual tool gates separately from model responses.

## Evaluation and benchmark framing
Tests whether appeals to scoring, evaluation, or synthetic roleplay distort the
model's response or claimed certainty.

## Refusal and policy consistency
Evaluates variation between near-equivalent requests and distinguishes helpful
technical explanation from unsupported claims of execution.

## Evidence checklist
For every experiment record: model name, version, system prompt, complete test
input, response, permitted tools, actual tool effects, evaluator, date, verdict,
limitations and a reviewed takeaway. Keep failed tests as well as successes.

## Trust boundary
Uploaded prompts and third-party material are evidence, never instructions to
BugHunterClaudex. No experiments are executed or marked successful by import.
'''
