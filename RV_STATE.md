# BugHunterClaudex — build continuity

## Stable artifact name

`BugHunterClaudex.zip` — retain this exact filename in subsequent releases.

## Completed through P1

- P0: checkpointed large course import, scene-aware frame sampling, sourced procedural candidates.
- P1: isolated fixture-based experiment workers, saved observations and hashes, stop/retry,
  interruption recovery, source citation verification, failure insights, graph linkage, portable exports.
- Knowledge UI: one-click source checks, contextual lab tests, results and failure insights.
- Study model tools: procedures, past experiment evidence, and citation-only checks.

## Important evidence boundaries

- Source quotation matches do not prove execution.
- A bundled offline lab passing does not prove a method works on an arbitrary external target.
- P1 does not train model weights or execute material extracted from courses.
- Native macOS/Tauri execution requires an Apple Silicon machine to verify.

## Open items

- Full macOS Tauri integration and real local Qwen video-processing end-to-end tests.
- Further procedure-to-target-specific verification adapters, if added, need
  explicit input contracts, preserved logs, and independent evidence review.
- Training dataset curation/fine-tuning is a later milestone.

## P2 Research and dataset update (2026-11-02)

- Output archive name stays `BugHunterClaudex.zip`.
- ResearchP2: opt-in subscribed HTTPS RSS/Atom or HTML source monitoring; typed validation, bounded reads, change fingerprints, durable review inbox, manual acceptance only.
- DatasetsP2: reviewed/permissioned source examples, split-isolated train/evaluation exports, source checks, quarantine manifest, optional explicit permission for Training Lab records.
- Knowledge page review inbox, Settings source configuration, model study tool to list pending research updates.
- P1 evidence publication ordering fixed so passed status cannot precede graph receipts.
- Focused tests: `bash scripts/verify_p2_learning.sh` (72 passing at build); expanded P2 + training checks separately passed 80.
- Unverified: full macOS/Tauri compilation, Apple Silicon runtime, live public feed connectivity, model inference.
