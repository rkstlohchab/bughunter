# Learning, storage, and portability

## What “learn” means here

The application stores extracted passages in SQLite, with source hashes, page/time/slide/sheet locators, corrections, and graph assertions. FTS5 searches the actual passages; optional local embeddings add semantic retrieval. Graph edges connect sources, examples, tools, prerequisites, and techniques. Initial links come from a vocabulary matcher; additional model-proposed claims must quote an existing source passage and remain marked inferred. A recorded investigation can add a reviewed observation.

This is retrieval-augmented knowledge, not model training. A model receives relevant passages through tools when it investigates. It does not permanently memorize the entire library. A graph helps organize and trace evidence, but does not prove that every concept or demonstration was understood. Courses about programming or other disciplines can be indexed as knowledge; importing them does not create additional execution capabilities.

## Local storage and GitHub

The normal library lives in `~/Library/Application Support/BugHunterClaudex/`, outside the source repository. `workbench.sqlite3` contains imported source records, extracted passages, citations, and graph data; `sources/` and `artifacts/` contain managed files. `BUGHUNTER_DATA_DIR` can select a different location. Large-folder imports may reference originals in your chosen course folder.

Importing a course does not commit or push anything to GitHub. The repository ignores SQLite files, build outputs, and local verification artifacts. Code and deliberately bundled reference documents can be versioned separately; those are not the personal imported library. A Git push is not a library backup. Use the app's local backup or portable knowledge export when you want a copy, and explicitly choose where to store or share it.

For a new web-testing course, import its folder under the intended project, wait for all files to reach a terminal status, then inspect extraction notes and sample searches. Missing transcripts, unreadable documents, or sampled-only visuals must remain visible as coverage gaps. Select that project in Chat to retrieve its sources. Importing and studying material does not establish that every lesson was understood or change model weights.

## Retrieval

Search is hybrid: SQLite FTS5 with the Porter stemmer (so "IDOR"/"IDORs", "inject"/"injection"/"injecting", and similar plural and verb/noun forms match each other automatically) plus an optional local-embedding pass when a model is configured. On top of that, a small acronym/terminology table (`SYNONYMS` in `knowledge.py`) expands things stemming can't bridge - BOLA/IDOR, SSRF, XSS, CSRF, SQLi, RCE, JWT, TOCTOU/race condition, and a few others - by adding the mapped terms as extra OR clauses alongside the person's own words, never in place of them. The concept graph's lexical vocabulary (`CONCEPTS`) covers the same дефект classes plus common techniques and tools (Burp, Nuclei, Cheat Engine, Ghidra, x64dbg, Frida), so newly imported material about them gets linked automatically; it is still explicitly `inferred`, same as before. An install from before the Porter tokenizer existed is migrated in place the next time it opens - `knowledge_fts` is rebuilt from `knowledge_chunks` (the source of truth), no re-import required.

For Markdown documents, subsequent chunks in a long section now retain the most recent heading as searchable **section metadata**. The original passage text stays unchanged, so verbatim-quote checks and citations still refer to the source, not inserted heading text. Previously imported passages gain this metadata when their source is reprocessed; legacy records without section metadata remain readable.

## Import choices

- **Files or ZIP:** select several files or drop them into the importer. Uploads are limited to 64 MiB each. ZIP entries keep filenames in their citations and have separate success/error results. Macro/script/formula content is never executed.
- **Website link:** imports one public HTML page or directly linked supported file. Every redirect and DNS destination is validated, with a 64 MiB download cap. The importer does not log in, run page JavaScript, follow every link, or use browser cookies. Unsupported responses and missing content produce errors or extraction notes. YouTube uses timestamped captions and optional explicitly requested media download.
- **Large course / folder:** reads existing local files without making a duplicate full copy. Keep them unchanged and accessible until processing finishes. A file can be up to 32 GiB; folders can contain up to 500 nonhidden regular files. Ingestion stays sequential. Unpack large course archives before importing their folder.

Supported extraction covers PDF pages, EPUB sections, DOCX paragraphs, PPTX slides/notes, XLSX sheet rows/cells, OpenDocument text, HTML, plain text/code, images, SRT/VTT, and common audio/video formats. Scanned pages and images need Tesseract for OCR; video uses FFmpeg and local whisper.cpp for speech recognition. Matching subtitle sidecars can supply timestamps without speech recognition. Video frames are sampled, not exhaustively analyzed. Missing tools are reported; no speech model is downloaded automatically.

Long recordings are transcribed in chunks of at most ten minutes. Each successful chunk keeps an atomic transcript checkpoint tied to the source and speech-model contents, with timestamps mapped back to the original recording. Incomplete audio/transcript intermediates are discarded. Library progress distinguishes transcription from frame analysis; retry reuses completed chunks. Global Stop all interrupts extraction subprocesses, including the adaptive scene scan. Transcription and OCR can misread content; a completed import is not an accuracy certification.

Tutorial analysis combines a sampled screenshot with up to two earlier screenshots and the nearby timestamped narration. The local vision model separates visible facts, narrator statements, changes between frames, inferred tutorial intent and reusable lessons, and missing evidence. Saved interpretations retain their image timestamps, narrator excerpts, model identifier and evidence fingerprint. Older interpretations without this context are reanalysed. Descriptions remain inferred; speech is not proof that a click or result appeared on screen.

Completed speech is searchable before the scene scan starts. Visual observations and OCR are published after the first frame, every ten frames, and at completion. These updates preserve existing corrections, citations, manual interpretations and source-backed notes. Library progress counts contextual observations separately from OCR-only frames. Model failure preserves speech and OCR while reporting missing visual interpretation; it never reports full visual understanding. Sampled analysis can miss brief actions between frames.

Archives are bounded to 1,000 entries, 256 MiB cumulative expanded data, three ZIP nesting levels, and a compression-ratio limit. Text parsing is capped at 20 MiB per file/XML part and 10 million extracted characters per source. Password-protected, unsafe, legacy binary, or unknown formats must be unlocked/exported into a supported format first. The app accepts arbitrary file selections but cannot parse every possible file format.

## Reclaiming disk space

1. Inspect a source's text, citations, warnings, and sampled images. A “ready” text extraction is not a certification of complete understanding.
2. Export the knowledge you want to preserve and keep a backup.
3. Select **Free source storage → Keep knowledge and release**. Partial sources require explicit acknowledgment of the gaps. Sources without searchable passages cannot release their originals.

This removes app-owned original copies and intermediate audio/downloads/extracted archive members. It retains indexed passages, extraction checkpoints, transcripts, corrections, graph records, and sampled images. External originals are never removed. After checking the extracted knowledge, you can move or delete those external files yourself. The app records that the original was released; importing it again is required for re-extraction. Public URL sources can instead be fetched again, but the remote material may have changed.

Keeping a few screenshots/transcripts is smaller than keeping video, but it loses unsampled demonstrations and audio details. Preserve originals elsewhere if those details may matter. The library should not be described as a lossless compressed course or trained model.

## Portable corpus and training preparation

**Library → How learning and storage work → Export portable knowledge** creates a ZIP in the local exports folder. The interface shows its exact path. Version 1 contains:

- `manifest.json`: schema version, creation time, counts, interpretation rules, and limitations.
- `sources.jsonl`, `passages.jsonl`: source metadata, original/corrected text, citation locators, and modality metadata.
- `nodes.jsonl`, `edges.jsonl`, `corrections.jsonl`: graph structure, provenance/review status, and correction history.
- `examples.jsonl`: all curated examples and their review/permission flags.
- `train.jsonl`, `evaluation.jsonl`: only reviewed, training-permitted question/answer pairs, with source/passage references. Evaluation questions stay outside training. Identical normalized questions cannot enter both splits; near-duplicate/manual dataset review remains necessary.
- `frames.jsonl` and optional image files: sampled visual evidence. Images are not automatically redacted. They are excluded by default in the desktop export.

The export excludes original course media, provider keys, target credentials/settings, and model-specific embedding vectors. Text uses the application's credential redaction rules; review any export before sharing. Rebuild embeddings with the destination embedding model. This corpus is an interchange artifact; it is not an automatic native-database restore operation.

On a source, **Create training example** lets you write a useful question and checked answer grounded in a passage, record permission to train, and choose train/evaluation. The app does not manufacture validated labels from every transcript. Correcting the cited passage makes its examples require fresh review. Deleting a source removes its curated examples. Training exports may be empty until you curate examples.

A future training pipeline must adapt the JSONL schema to its trainer, check quality and permissions, remove duplicates, select supported tasks, and evaluate held-out examples. No fine-tuning is performed by this release. Existing Claude Code, explicitly configured API providers, and compatible local models can use the knowledge through retrieval now. Browser chat products do not automatically gain access to files on this Mac.
# Bundled workbench reference pack

The `workbench-methods-v1` pack adds ten authored notes covering commerce and food
quotes, API ownership and sessions, GraphQL classification, injection evidence,
HTTP destinations, race/workflow case studies, native memory lifetimes, PE and
projection conventions, and retrieval provenance. Its manifest records document
hashes and 15 primary-source links checked on 2026-11-11. Historical published
cases, local worked examples, and suggested tests are explicitly distinguished.
These are unreviewed reference syntheses, not trained weights or an exhaustive
domain corpus.

The primary backend imports the pack once on startup using a versioned seed
flag. Deleting a source does not recreate it on the next launch. Explicitly
restore/import it with desktop RPC `knowledge.reference_pack.import` (optional
`project_id`), or, with the app closed:

```sh
backend/.venv/bin/python scripts/import_reference_pack.py
```

The importer uses the existing extraction/indexing pipeline and content-based
deduplication. It validates the complete manifest before importing, and preserves
project scope. The default destination is the app's normal data directory;
`--data-dir DIR` selects an isolated library. PyInstaller packaging includes the
same assets. No source websites or model APIs are needed to ingest the text;
optional embeddings follow the library's existing local-model settings.

Run the repeatable loopback HTTP and retrieval check with a **new** output path:

```sh
backend/.venv/bin/python scripts/smoke_commerce_knowledge.py --output artifacts/commerce-knowledge-check
```

The JSON report retains 34 paired commerce/food requests and their receipts,
17 deliberately дефектный outcomes and 17 fixed controls, source counts,
retrieval probes, and the isolated database. It does not test external websites,
payments, or concurrent coupon redemption. Native lifecycle tests use Mach API
doubles and do not establish real target-process compatibility.

### Progressive course evidence and review

Speech uses atomic ten-minute SRT checkpoints keyed by source bytes/stat and model
bytes. Each completed chunk becomes searchable immediately, including after a
later chunk fails. `sources.prepare_audio` (`{"id":"source_…"}`) runs this speech
pass synchronously for a local source and leaves `stage=audio_complete`,
`status=partial`; the normal source worker subsequently reuses those checkpoints
and performs visual processing. It refuses an already processing source.

Adaptive scene detection uses ten-minute windows in `scene-cuts-v2.json`; each
window records completion or its error. Retry reuses completed windows and retries
gaps. Threshold, source hash/stat, duration and scan version identify the cache.
Old bulk scan files do not establish completion. `scene_coverage`,
`speech_coverage`, and `media_coverage` describe completed work and missing evidence;
a timeout is never reported as a successfully empty scene scan.

`knowledge.reject` (`{"id":"chunk_…","reason":"…"}`) retains the passage and
its original text, records an audit event, removes active keyword/vector retrieval,
and marks generated graph assertions inactive. Frame timeline entries are archived
in `rejected-<chunk_id>.json`; the final extraction cache is invalidated. Original
media is retained. Source detail includes inactive records for audit. Reprocessing
never reactivates an identical rejected passage. Changed visual context/provider/
model/evidence generation supersedes older generated passages while preserving
manual corrections and their learned references. All chunks of one current frame
remain active together. A frame explicitly described as unreadable receives at
most one additional attempt at up to 2000px; its image hash makes the checkpoint
resolution aware. Provider failures preserve completed evidence and fail the pass.
