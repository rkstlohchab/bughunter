# Learning, storage, and portability

## What “learn” means here

The application stores extracted passages in SQLite, with source hashes, page/time/slide/sheet locators, corrections, and graph assertions. FTS5 searches the actual passages; optional local embeddings add semantic retrieval. Graph edges connect sources, examples, tools, prerequisites, and techniques. Initial links come from a vocabulary matcher; additional model-proposed claims must quote an existing source passage and remain marked inferred. A recorded investigation can add a reviewed observation.

This is retrieval-augmented knowledge, not model training. A model receives relevant passages through tools when it investigates. It does not permanently memorize the entire library. A graph helps organize and trace evidence, but does not prove that every concept or demonstration was understood. Courses about programming or other disciplines can be indexed as knowledge; importing them does not create additional execution capabilities.

## Retrieval

Search is hybrid: SQLite FTS5 with the Porter stemmer (so "IDOR"/"IDORs", "inject"/"injection"/"injecting", and similar plural and verb/noun forms match each other automatically) plus an optional local-embedding pass when a model is configured. On top of that, a small acronym/terminology table (`SYNONYMS` in `knowledge.py`) expands things stemming can't bridge - BOLA/IDOR, SSRF, XSS, CSRF, SQLi, RCE, JWT, TOCTOU/race condition, and a few others - by adding the mapped terms as extra OR clauses alongside the person's own words, never in place of them. The concept graph's lexical vocabulary (`CONCEPTS`) covers the same vulnerability classes plus common techniques and tools (Burp, Nuclei, Cheat Engine, Ghidra, x64dbg, Frida), so newly imported material about them gets linked automatically; it is still explicitly `inferred`, same as before. An install from before the Porter tokenizer existed is migrated in place the next time it opens - `knowledge_fts` is rebuilt from `knowledge_chunks` (the source of truth), no re-import required.

For Markdown documents, subsequent chunks in a long section now retain the most recent heading as searchable **section metadata**. The original passage text stays unchanged, so verbatim-quote checks and citations still refer to the source, not inserted heading text. Previously imported passages gain this metadata when their source is reprocessed; legacy records without section metadata remain readable.

## Import choices

- **Files or ZIP:** select several files or drop them into the importer. Uploads are limited to 64 MiB each. ZIP entries keep filenames in their citations and have separate success/error results. Macro/script/formula content is never executed.
- **Website link:** imports one public HTML page or directly linked supported file. Every redirect and DNS destination is validated, with a 64 MiB download cap. The importer does not log in, run page JavaScript, follow every link, or use browser cookies. Unsupported responses and missing content produce errors or extraction notes. YouTube uses timestamped captions and optional explicitly requested media download.
- **Large course / folder:** reads existing local files without making a duplicate full copy. Keep them unchanged and accessible until processing finishes. A file can be up to 32 GiB; folders can contain up to 500 nonhidden regular files. Ingestion stays sequential. Unpack large course archives before importing their folder.

Supported extraction covers PDF pages, EPUB sections, DOCX paragraphs, PPTX slides/notes, XLSX sheet rows/cells, OpenDocument text, HTML, plain text/code, images, SRT/VTT, and common audio/video formats. Scanned pages and images need Tesseract for OCR; video uses FFmpeg and local whisper.cpp for speech recognition. Matching subtitle sidecars can supply timestamps without speech recognition. Video frames are sampled, not exhaustively analyzed. Missing tools are reported; no speech model is downloaded automatically.

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
