"""game_offsets — AOB signature scanner / offset refresher.

Three paths:
- ``refresh`` — pull latest offsets for a game from a known community source
  (cs2-dumper releases, hazedumper, etc.) and normalize into our JSON schema.
- ``scan``    — AOB-scan a dumped game image on disk using a per-game
  signature set under ``cheats/dma-stack/signatures/<game>.sig.json``.
- ``list``    — show which games we have signature sets or dumpers for.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

from ._common import envelope


COMMUNITY_SOURCES = {
    "cs2": {
        "name": "a2x/cs2-dumper",
        "api": "https://api.github.com/repos/a2x/cs2-dumper/releases/latest",
        "assets": ["offsets.json"],
    },
    # Space to add more: hazedumper for cs:go; unreal engine dumpers for fortnite etc.
}


def run(app, *, mode: str = "list",
        game: str | None = None,
        source: str = "auto",
        image_path: str | None = None,
        process_name: str | None = None,
        **_) -> dict:
    if mode == "list":
        return _list(app)
    if mode == "refresh":
        if not game:
            raise ValueError("refresh requires game")
        return _refresh(app, game=game, source=source)
    if mode == "scan":
        if not game or not image_path:
            raise ValueError("scan requires game + image_path")
        return _scan(app, game=game, image_path=image_path)
    raise ValueError(f"unknown mode: {mode}")


def _list(app) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    sigs_dir = repo_root / "cheats" / "dma-stack" / "signatures"
    sigs = sorted([p.stem.replace(".sig", "") for p in sigs_dir.glob("*.sig.json")])
    offsets_dir = Path(app.store.root) / "gamelab_offsets"
    have_offsets = sorted([p.stem for p in offsets_dir.glob("*.json")]) if offsets_dir.exists() else []
    return envelope("game_offsets",
                    {"command": "list", "exit_code": 0,
                     "stdout": "", "stderr": "", "duration_ms": 0},
                    community_sources=list(COMMUNITY_SOURCES.keys()),
                    signature_sets=sigs,
                    have_offsets=have_offsets)


def _refresh(app, *, game: str, source: str) -> dict:
    if source == "auto":
        if game in COMMUNITY_SOURCES:
            return _refresh_from_community(app, game=game)
        return _refresh_from_scan(app, game=game)
    if source == "scan":
        return _refresh_from_scan(app, game=game)
    if source == "community":
        if game not in COMMUNITY_SOURCES:
            return envelope("game_offsets",
                            {"command": "refresh", "exit_code": -1,
                             "stdout": "", "stderr": f"no community source for {game}",
                             "duration_ms": 0}, offsets={})
        return _refresh_from_community(app, game=game)
    raise ValueError(f"unknown source: {source}")


def _refresh_from_community(app, *, game: str) -> dict:
    src = COMMUNITY_SOURCES[game]
    try:
        req = urllib.request.Request(src["api"],
                                     headers={"User-Agent": "bughunter-gamelab"})
        with urllib.request.urlopen(req, timeout=15) as rsp:
            rel = json.loads(rsp.read().decode())
    except Exception as exc:
        return envelope("game_offsets",
                        {"command": "refresh", "exit_code": -1,
                         "stdout": "", "stderr": f"github api: {exc}",
                         "duration_ms": 0}, offsets={})
    offsets_raw = None
    for asset in rel.get("assets", []):
        if asset["name"] in src["assets"]:
            try:
                req = urllib.request.Request(asset["browser_download_url"],
                                             headers={"User-Agent": "bughunter-gamelab"})
                with urllib.request.urlopen(req, timeout=30) as rsp:
                    offsets_raw = rsp.read().decode("utf-8", errors="ignore")
                    break
            except Exception as exc:
                return envelope("game_offsets",
                                {"command": "refresh", "exit_code": -1,
                                 "stdout": "", "stderr": f"asset dl: {exc}",
                                 "duration_ms": 0}, offsets={})
    if offsets_raw is None:
        return envelope("game_offsets",
                        {"command": "refresh", "exit_code": -1,
                         "stdout": "", "stderr": "no offsets.json asset",
                         "duration_ms": 0}, offsets={})
    try:
        offsets = json.loads(offsets_raw)
    except Exception:
        offsets = {"raw": offsets_raw[:200_000]}
    return envelope("game_offsets",
                    {"command": "refresh", "exit_code": 0,
                     "stdout": f"release={rel.get('tag_name', 'unknown')}",
                     "stderr": "", "duration_ms": 0},
                    offsets=offsets, release=rel.get("tag_name", ""),
                    notes=f"from {src['name']}")


def _refresh_from_scan(app, *, game: str) -> dict:
    """Trigger a live AOB scan by delegating to the DMA stack's offset_finder."""
    # This requires a live game + a configured DMA reader. Here we just
    # surface the call plan; the GameLab UI's "Refresh offsets" button
    # routes to dma_memread adapter with mode=offset_scan when DMA is
    # present.
    return envelope("game_offsets",
                    {"command": "refresh", "exit_code": 0,
                     "stdout": "no live-scan path from this adapter — "
                                "run dma_memread mode=offset_scan to AOB-scan "
                                "a running process",
                     "stderr": "", "duration_ms": 0}, offsets={})


def _scan(app, *, game: str, image_path: str) -> dict:
    """AOB-scan a dumped image file on disk against the signature set."""
    repo_root = Path(__file__).resolve().parents[3]
    sigs_path = repo_root / "cheats" / "dma-stack" / "signatures" / f"{game}.sig.json"
    if not sigs_path.exists():
        return envelope("game_offsets",
                        {"command": "scan", "exit_code": -1,
                         "stdout": "", "stderr": f"no signatures for {game}",
                         "duration_ms": 0}, offsets={})
    try:
        sigs = json.loads(sigs_path.read_text())
    except Exception as exc:
        return envelope("game_offsets",
                        {"command": "scan", "exit_code": -1,
                         "stdout": "", "stderr": f"bad sig json: {exc}",
                         "duration_ms": 0}, offsets={})
    img = Path(image_path)
    if not img.exists():
        return envelope("game_offsets",
                        {"command": "scan", "exit_code": -1,
                         "stdout": "", "stderr": "image not found",
                         "duration_ms": 0}, offsets={})
    data = img.read_bytes()
    results: dict[str, dict] = {}
    for name, entry in sigs.items():
        pat = entry.get("pattern", "")
        rel = entry.get("rel_offset", 0)
        read_bytes = entry.get("read", 4)
        regex = _pattern_to_regex(pat)
        m = regex.search(data)
        if m:
            addr = m.start() + rel
            payload = data[addr:addr + read_bytes]
            results[name] = {"found_at": m.start(), "resolved": addr,
                             "bytes": payload.hex()}
        else:
            results[name] = {"found_at": None}
    return envelope("game_offsets",
                    {"command": "scan", "exit_code": 0,
                     "stdout": f"{sum(1 for v in results.values() if v['found_at'] is not None)} / {len(results)}",
                     "stderr": "", "duration_ms": 0},
                    offsets=results, image=str(img))


def _pattern_to_regex(pat: str) -> re.Pattern:
    """Convert IDA-style pattern "48 8B ? ? C3" to a compiled bytes regex."""
    parts = pat.strip().split()
    out = []
    for p in parts:
        if p in ("?", "??"):
            out.append(b".")
        else:
            out.append(re.escape(bytes.fromhex(p)))
    return re.compile(b"".join(out), re.DOTALL)
