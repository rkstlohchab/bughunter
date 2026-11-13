"""Learning Forge — turn everything the operator has done into training pairs.

The forge assembles a training corpus from four streams:

1. TraceMiner   — past RunManager transcripts (SFT: prompt → tools → answer)
2. ErrorMiner   — paired failure/success for DPO/ORPO preference training
3. RepoMiner    — the operator's own source tree (FIM + explain pairs)
4. Distiller    — frontier-model synthesis of (q, a) pairs from library chunks

Output is sharded JSONL under ``<data>/exports/corpus/<timestamp>/`` with a
``manifest.json`` recording weights, sizes, provenance.

All heavy I/O stays on the Mac; training itself lives on Kaggle (see
``kaggle_finetune.py``). The forge NEVER runs an LLM locally on M1 for
training — only reads, chunks, pairs, writes.
"""
from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator, Any

from .store import Store, now, new_id
from .security import redact_text


# ----------------------------- pair types ------------------------------------


@dataclass
class SFTPair:
    """Supervised fine-tuning example: a conversation."""
    messages: list[dict]            # [{role, content}]
    source: str                     # provenance URL / path / entity id
    domain: str                     # web-audit | binary-re | mobile | network | memory | osint | codebase | dj-voice
    tags: list[str] = field(default_factory=list)
    weight: float = 1.0
    hash: str | None = None

    def digest(self) -> str:
        blob = json.dumps(self.messages, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class DPOPair:
    """Preference pair for DPO/ORPO."""
    prompt: str
    chosen: str
    rejected: str
    source: str
    domain: str
    reason: str                     # why chosen beat rejected
    weight: float = 1.0
    hash: str | None = None

    def digest(self) -> str:
        return hashlib.sha256(
            (self.prompt + "\x1f" + self.chosen + "\x1f" + self.rejected).encode("utf-8")
        ).hexdigest()


# ----------------------------- miners ----------------------------------------


class TraceMiner:
    """Mine SFT pairs from past investigation / operator / study transcripts.

    Signal is the operator's real completed work. Each run produces up to one
    (system, user, assistant_with_tool_calls*) conversation.
    """

    def __init__(self, store: Store):
        self.store = store

    def iter_pairs(self, limit: int | None = None) -> Iterator[SFTPair]:
        count = 0
        for run in self.store.list("run"):
            if limit and count >= limit:
                return
            if run.get("status") not in {"completed", "finished", "done", "ok"}:
                continue
            transcript = run.get("storable_transcript") or run.get("transcript") or []
            if not isinstance(transcript, list) or len(transcript) < 2:
                continue
            # keep roles the trainer understands
            cleaned = []
            for msg in transcript:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                if role not in {"system", "user", "assistant", "tool"}:
                    continue
                if isinstance(content, list):  # tool-call blocks
                    content_text = _flatten_content_blocks(content)
                elif isinstance(content, str):
                    content_text = content
                else:
                    continue
                if not content_text:
                    continue
                content_text = redact_text(content_text)[:8192]
                cleaned.append({"role": role, "content": content_text,
                                "tool_calls": msg.get("tool_calls")})
            if len(cleaned) < 2:
                continue
            domain = _classify_run_domain(run)
            pair = SFTPair(
                messages=cleaned,
                source=f"run:{run['id']}",
                domain=domain,
                tags=["trace", run.get("kind", "investigation")],
                weight=2.0 if run.get("review_state") == "verified" else 1.0,
            )
            pair.hash = pair.digest()
            yield pair
            count += 1


class ErrorMiner:
    """Mine DPO preference pairs from failure↔success patterns.

    Pairs two runs with similar prompts where one failed/denied and one
    completed. The successful final answer is 'chosen'; the failed one is
    'rejected'. Limits to ~top_k pairs per domain to keep the dataset balanced.
    """

    def __init__(self, store: Store):
        self.store = store

    def iter_pairs(self, top_k: int = 500) -> Iterator[DPOPair]:
        runs = self.store.list("run")
        buckets: dict[str, dict[str, list[dict]]] = {"completed": {}, "failed": {}}
        for run in runs:
            status = run.get("status", "")
            bucket = ("completed" if status in {"completed", "finished", "done", "ok"}
                      else "failed" if status in {"failed", "denied", "cancelled", "error"}
                      else None)
            if bucket is None:
                continue
            key = _prompt_key(run.get("prompt", ""))
            if not key:
                continue
            buckets[bucket].setdefault(key, []).append(run)
        yielded = 0
        for key, failures in buckets["failed"].items():
            if yielded >= top_k:
                return
            successes = buckets["completed"].get(key, [])
            if not successes:
                continue
            good = successes[0]
            bad = failures[0]
            good_answer = _final_assistant(good)
            bad_answer = _final_assistant(bad) or bad.get("error", "failed")
            if not good_answer:
                continue
            pair = DPOPair(
                prompt=redact_text(good.get("prompt", ""))[:4096],
                chosen=redact_text(good_answer)[:4096],
                rejected=redact_text(bad_answer)[:4096],
                source=f"runs:{good['id']}>{bad['id']}",
                domain=_classify_run_domain(good),
                reason=bad.get("failure_reason") or bad.get("status") or "diverged",
            )
            pair.hash = pair.digest()
            yield pair
            yielded += 1


class RepoMiner:
    """Mine FIM (fill-in-middle) and explain pairs from the operator's source tree.

    Teaches the local model the project's own conventions, file layout and APIs.
    FIM uses the standard prefix/suffix/middle markers that most code models
    were pretrained with.
    """

    FIM_PREFIX = "<fim_prefix>"
    FIM_SUFFIX = "<fim_suffix>"
    FIM_MIDDLE = "<fim_middle>"

    SKIP_DIRS = {"node_modules", "target", "dist", "build", ".git", ".venv",
                 "__pycache__", "exports", "sources", ".cache", ".npm", ".pytest_cache"}
    INCLUDE_EXT = {".py", ".ts", ".tsx", ".rs", ".sh", ".md", ".sql", ".toml"}

    def __init__(self, repo_root: Path):
        self.repo = Path(repo_root)

    def iter_pairs(self, max_files: int = 2000) -> Iterator[SFTPair]:
        count = 0
        for path in self.repo.rglob("*"):
            if count >= max_files:
                return
            if not path.is_file():
                continue
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            if path.suffix not in self.INCLUDE_EXT:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip() or len(text) > 200_000:
                continue
            rel = path.relative_to(self.repo)
            # explain pair
            yield from self._explain_pair(str(rel), text)
            # FIM pair
            fim = self._fim_pair(str(rel), text)
            if fim is not None:
                yield fim
            count += 1

    def _explain_pair(self, rel: str, text: str) -> Iterator[SFTPair]:
        snippet = text[:6000]
        pair = SFTPair(
            messages=[
                {"role": "user",
                 "content": f"Explain what `{rel}` in the bughunter project does, "
                            f"at module level. Reference symbols where helpful.\n\n"
                            f"```\n{snippet}\n```"},
                {"role": "assistant",
                 "content": _auto_summary(rel, snippet)},
            ],
            source=f"repo:{rel}",
            domain="codebase",
            tags=["explain", rel.split("/")[0] if "/" in rel else "root"],
        )
        pair.hash = pair.digest()
        yield pair

    def _fim_pair(self, rel: str, text: str) -> SFTPair | None:
        if len(text) < 400:
            return None
        n = len(text)
        a = n // 3
        b = (n * 2) // 3
        prefix, middle, suffix = text[:a], text[a:b], text[b:]
        pair = SFTPair(
            messages=[
                {"role": "user",
                 "content": f"{self.FIM_PREFIX}{prefix}{self.FIM_SUFFIX}{suffix}{self.FIM_MIDDLE}"},
                {"role": "assistant", "content": middle},
            ],
            source=f"repo:{rel}#fim",
            domain="codebase",
            tags=["fim", rel.split("/")[0] if "/" in rel else "root"],
            weight=0.5,
        )
        pair.hash = pair.digest()
        return pair


class Distiller:
    """Optional: route library chunks through a frontier model to synthesize
    (question, ideal_answer, citations) training pairs.

    Uses the already-configured provider from ``ProviderSettings``. This runs
    in batches and is designed to be invoked manually/offline — not on hot
    chat paths, and never on an 8GB M1 (routes to ``remote_ollama``/hosted).
    """

    def __init__(self, store: Store, knowledge, settings):
        self.store = store
        self.knowledge = knowledge
        self.settings = settings

    def iter_pairs(self, max_chunks: int = 200,
                   domain_filter: str | None = None) -> Iterator[SFTPair]:
        from . import provider_api  # late import; avoid circular
        provider_cfg = self.settings.get()
        provider = provider_cfg.get("provider", "")
        # Local-only mode refuses hosted distillation; return nothing.
        if provider_cfg.get("local_only") and provider not in {"ollama", "crucible"}:
            return
        chunks = self._pick_chunks(max_chunks, domain_filter)
        generation=self.store.setting('stop_generation',0)
        def check():
            if self.store.setting('emergency_stop',False) or generation!=self.store.setting('stop_generation',0):raise InterruptedError('Dataset generation stopped.')
        for chunk in chunks:
            check()
            prompt = (
                "You are preparing supervised training data for a security research "
                "assistant. Given the passage below, write ONE realistic operator "
                "question that the passage answers, and a precise answer grounded in "
                "the passage. Quote at most 15 words from the passage. Return JSON: "
                '{"question": "...", "answer": "...", "citation": "..."}.\n\n'
                f"PASSAGE:\n{chunk.get('text','')[:4000]}"
            )
            try:
                response = provider_api.simple_completion(provider_cfg, prompt, max_tokens=800,check=check)
                check()
            except InterruptedError:raise
            except Exception:
                continue
            parsed = _parse_json_block(response)
            if not parsed or "question" not in parsed or "answer" not in parsed:
                continue
            pair = SFTPair(
                messages=[
                    {"role": "user", "content": parsed["question"][:4000]},
                    {"role": "assistant", "content": parsed["answer"][:4000]},
                ],
                source=f"distill:{chunk.get('id','?')}",
                domain=domain_filter or _classify_chunk_domain(chunk),
                tags=["distilled"],
                weight=0.8,
            )
            pair.hash = pair.digest()
            yield pair

    def _pick_chunks(self, n: int, domain: str | None) -> list[dict]:
        try:
            all_chunks = self.knowledge.list_chunks(limit=n * 3) if hasattr(
                self.knowledge, "list_chunks") else []
        except Exception:
            all_chunks = []
        if not all_chunks:
            # fall back to listing sources and reading their first chunk
            chunks: list[dict] = []
            for src in self.store.list("source")[: n * 2]:
                text = (src.get("summary") or src.get("content") or "")[:4000]
                if text:
                    chunks.append({"id": src.get("id"), "text": text,
                                   "source_id": src.get("id")})
            return chunks[:n]
        if domain:
            return [c for c in all_chunks if _classify_chunk_domain(c) == domain][:n]
        return all_chunks[:n]


# ----------------------------- orchestrator ----------------------------------


class LearningForge:
    """Orchestrates mining → merging → sharding → writing the corpus."""

    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.root = Path(self.store.root) / "exports" / "corpus"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # dispatch surface -----------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "forge.list":
            return self.list_bundles()
        if method == "forge.build":
            return self.build(**params)
        if method == "forge.get":
            return self.get(params["id"])
        if method == "forge.delete":
            return self.delete(params["id"])
        if method == "forge.preview":
            return self.preview(**params)
        raise ValueError(f"Unknown forge method: {method}")

    # core ----------------------------------------------------------------

    def preview(self, include: list[str] | None = None,
                limits: dict | None = None) -> dict:
        include = include or ["traces", "errors", "repo"]
        limits = limits or {}
        counts: dict[str, int] = {}
        if "traces" in include:
            counts["traces"] = sum(1 for _ in TraceMiner(self.store)
                                   .iter_pairs(limit=limits.get("traces")))
        if "errors" in include:
            counts["errors"] = sum(1 for _ in ErrorMiner(self.store)
                                   .iter_pairs(top_k=limits.get("errors", 500)))
        if "repo" in include:
            repo_root = Path(self.store.root).parent  # project root hint; see note
            counts["repo"] = 0  # live counted only during build; preview is fast
        return {"counts": counts, "data_root": str(self.root)}

    def build(self, include: list[str] | None = None,
              repo_root: str | None = None,
              distill: bool = False,
              distill_domain: str | None = None,
              limits: dict | None = None,
              name: str | None = None) -> dict:
        self.store.require_desktop_owner()
        generation=self.store.setting('stop_generation',0)
        def check():
            if self.store.setting('emergency_stop',False) or generation!=self.store.setting('stop_generation',0):raise InterruptedError('Corpus preparation stopped.')
        check()
        include = include or ["traces", "errors", "repo"]
        limits = limits or {}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bundle_id = new_id("corpus")
        slug = re.sub(r"[^a-z0-9_-]+", "-",
                      (name or stamp).lower()).strip("-") or stamp
        bundle_dir = self.root / f"{stamp}-{slug}"
        bundle_dir.mkdir(parents=True, exist_ok=False, mode=0o700)

        sft_path = bundle_dir / "train.jsonl"
        valid_path = bundle_dir / "valid.jsonl"
        dpo_path = bundle_dir / "preferences.jsonl"
        manifest_path = bundle_dir / "manifest.json"

        seen_hashes: set[str] = set()
        shard_counts: dict[str, int] = {"sft": 0, "dpo": 0}
        domain_counts: dict[str, int] = {}

        def emit_sft(p: SFTPair, writer):
            check()
            if p.hash in seen_hashes:
                return
            seen_hashes.add(p.hash)
            writer.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
            shard_counts["sft"] += 1
            domain_counts[p.domain] = domain_counts.get(p.domain, 0) + 1

        def emit_dpo(p: DPOPair, writer):
            check()
            if p.hash in seen_hashes:
                return
            seen_hashes.add(p.hash)
            writer.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
            shard_counts["dpo"] += 1

        with sft_path.open("w", encoding="utf-8") as sft_w, \
             dpo_path.open("w", encoding="utf-8") as dpo_w:
            if "traces" in include:
                for p in TraceMiner(self.store).iter_pairs(limit=limits.get("traces")):
                    emit_sft(p, sft_w)
            if "errors" in include:
                for p in ErrorMiner(self.store).iter_pairs(top_k=limits.get("errors", 500)):
                    emit_dpo(p, dpo_w)
            if "repo" in include and repo_root:
                for p in RepoMiner(Path(repo_root)).iter_pairs(
                        max_files=limits.get("repo", 2000)):
                    emit_sft(p, sft_w)
            if distill:
                try:
                    distiller = Distiller(self.store, self.app.knowledge, self.app.settings)
                    for p in distiller.iter_pairs(
                            max_chunks=limits.get("distill", 200),
                            domain_filter=distill_domain):
                        emit_sft(p, sft_w)
                except InterruptedError:raise
                except Exception as exc:
                    # distillation is optional — never block the build
                    (bundle_dir / "distill_error.txt").write_text(repr(exc))

        # split a holdout from the training set for valid.jsonl
        self._split_holdout(sft_path, valid_path, ratio=0.05)
        check()

        manifest = {
            "id": bundle_id,
            "name": slug,
            "created_at": now(),
            "data_dir": str(bundle_dir),
            "include": include,
            "counts": shard_counts,
            "domains": domain_counts,
            "limits": limits,
            "distill": bool(distill),
            "schema": "sft:openai-chat-messages;dpo:prompt-chosen-rejected",
            "fim_markers": {"prefix": RepoMiner.FIM_PREFIX,
                            "suffix": RepoMiner.FIM_SUFFIX,
                            "middle": RepoMiner.FIM_MIDDLE},
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        self.store.put("corpus", manifest, id=bundle_id)
        self.store.event("forge.build", {"id": bundle_id, "counts": shard_counts})
        return manifest

    def _split_holdout(self, src: Path, dst: Path, ratio: float = 0.05):
        import random
        rng = random.Random(0xB0ADD)
        if not src.exists():
            return
        lines = src.read_text(encoding="utf-8").splitlines()
        if len(lines) < 50:
            return
        rng.shuffle(lines)
        split = max(1, int(len(lines) * ratio))
        holdout = lines[:split]
        rest = lines[split:]
        dst.write_text("\n".join(holdout) + "\n", encoding="utf-8")
        src.write_text("\n".join(rest) + "\n", encoding="utf-8")

    def list_bundles(self) -> list[dict]:
        return self.store.list("corpus")

    def get(self, bundle_id: str) -> dict | None:
        return self.store.get("corpus", bundle_id)

    def delete(self, bundle_id: str) -> dict:
        bundle = self.store.get("corpus", bundle_id)
        if not bundle:
            raise ValueError("Unknown corpus bundle.")
        import shutil
        shutil.rmtree(bundle["data_dir"], ignore_errors=True)
        self.store.delete("corpus", bundle_id)
        self.store.event("forge.delete", {"id": bundle_id})
        return {"deleted": bundle_id}


# ----------------------------- helpers ---------------------------------------


def _flatten_content_blocks(blocks: list) -> str:
    """Flatten Anthropic-style content blocks to plain text."""
    out: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and "text" in b:
            out.append(b["text"])
        elif b.get("type") == "tool_use":
            out.append(f"[tool:{b.get('name','?')} {json.dumps(b.get('input', {}))}]")
        elif b.get("type") == "tool_result":
            value = b.get("content", "")
            if isinstance(value, list):
                value = _flatten_content_blocks(value)
            out.append(f"[result: {value}]")
    return "\n".join(out)


def _classify_run_domain(run: dict) -> str:
    prompt = (run.get("prompt") or "").lower()
    kind = (run.get("kind") or "").lower()
    if "memory" in kind or "memory" in prompt or "pointer" in prompt:
        return "memory"
    if "tor" in kind or "tor" in prompt:
        return "osint"
    if "mobile" in prompt or "android" in prompt or "ios" in prompt:
        return "mobile"
    if "nmap" in prompt or "scan" in prompt or "subdomain" in prompt:
        return "network"
    if "binary" in prompt or "ghidra" in prompt or "re" in kind:
        return "binary-re"
    return "web-audit"


def _classify_chunk_domain(chunk: dict) -> str:
    tags = (chunk.get("tags") or [])
    text = (chunk.get("text") or "").lower()[:2000]
    if any(t in str(tags).lower() for t in ("memory", "cheat", "ptr")):
        return "memory"
    if "android" in text or "frida" in text:
        return "mobile"
    if "nmap" in text or "subdomain" in text or "dns" in text:
        return "network"
    if "ghidra" in text or "ida" in text or "radare" in text:
        return "binary-re"
    return "web-audit"


def _prompt_key(prompt: str) -> str:
    """Normalize a prompt to a bucket key (for pairing failure↔success)."""
    if not prompt:
        return ""
    # lowercase, strip punctuation, collapse whitespace, first 10 tokens
    words = re.findall(r"[a-z0-9]+", prompt.lower())[:10]
    return " ".join(words)


def _final_assistant(run: dict) -> str:
    transcript = run.get("storable_transcript") or run.get("transcript") or []
    for msg in reversed(transcript):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            c = msg.get("content")
            if isinstance(c, list):
                return _flatten_content_blocks(c)
            if isinstance(c, str):
                return c
    return ""


def _parse_json_block(text: str) -> dict | None:
    if not text:
        return None
    # tolerate ```json fences
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    m = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _auto_summary(rel: str, text: str) -> str:
    """A cheap, deterministic placeholder summary used by RepoMiner.

    Training is better signal than hand-written summaries. For the explain
    pair we produce a stable, machine-readable description from docstring +
    symbol list, which the model then learns to generate from the file alone.
    """
    lines = text.splitlines()
    doc = ""
    if lines and lines[0].startswith('"""'):
        try:
            idx = next(i for i in range(1, len(lines))
                       if lines[i].rstrip().endswith('"""'))
            doc = "\n".join(lines[: idx + 1]).strip('"').strip()
        except StopIteration:
            pass
    symbols = re.findall(r"^(?:def|class|fn|pub fn|export function|function|struct)\s+([A-Za-z_][A-Za-z0-9_]*)",
                         text, re.MULTILINE)
    symbols = list(dict.fromkeys(symbols))[:20]
    pieces = [f"File `{rel}` in the bughunter workbench."]
    if doc:
        pieces.append(doc[:1000])
    if symbols:
        pieces.append("Exposes: " + ", ".join(f"`{s}`" for s in symbols) + ".")
    return " ".join(pieces)
