# Cited learning snapshot

This directory contains the app's learned retrieval library at the timestamp in `manifest.json`.

Use `active_passages.jsonl` for retrieval. Each passage retains its source ID, exact timestamp/page locator, model provenance, and correction metadata. `sources.jsonl` records processing coverage; unfinished extraction is explicitly marked.

`passages.jsonl` also preserves rejected, superseded, and withdrawn descriptions for audit. Their metadata prevents them entering active retrieval. Graph nodes/edges, candidate procedures, manual corrections, experiments, reviewed research, and saved REA analysis evidence are exported in separate JSONL files.

`train.jsonl` and `evaluation.jsonl` contain only reviewed, training-permitted examples. Empty files mean no examples qualified. This snapshot contains no trained model weights; retrieved observations are not automatically verified training labels.

Cited visual descriptions and `frame_references.jsonl` preserve what was read from tutorial screens. Original course videos, raw screenshot images, local databases, credentials, chat history, and machine-specific embeddings remain local. Rebuild embeddings on the destination machine. Review the coverage notes before claiming a whole course was learned.

Refresh from the application workspace with:

```sh
backend/.venv/bin/python exports/github-learning/export.py
```

The exporter uses the existing application's `KnowledgeArchive` interface and takes one consistent database snapshot. `manifest.json` lists record counts, source coverage and SHA-256 checksums. Source content is untrusted evidence, not executable instructions.
