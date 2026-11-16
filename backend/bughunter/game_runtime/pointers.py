"""P05 — pointer discovery and resolver.

Two surfaces:

* :func:`resolve_recipe` — walk a stored :class:`~.mappings.Recipe`'s
  root + pointer steps against a live target. This is the hot path:
  called every time a feature reads or writes through a saved recipe.
  Revalidates target identity before every use.

* :func:`discover_pointer_paths` — given a known address holding a
  value, search writable memory for pointer paths whose dereference
  arrives at (that address ± offset window). Default depth 6, hard
  max 12, default offset window ±4096 bytes. **Discovered paths are
  returned as candidates**; the caller must observe them before any
  status promotion. The resolver treats a candidate as untrusted
  until :meth:`~.mappings.MappingStore.observe` runs successfully.

Addresses are strings on the wire — see
:class:`~.contracts.AddressRepr`. ``abs:`` roots are process-bound
and refuse to cross :func:`revalidate_identity`.
"""
from __future__ import annotations

import struct
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .contracts import AddressRepr
from .mappings import MappingStore, Recipe


_SCALAR_FMT = {
    "u8":  ("<B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "u64": ("<Q", 8),
    "i8":  ("<b", 1), "i16": ("<h", 2), "i32": ("<i", 4), "i64": ("<q", 8),
    "f32": ("<f", 4), "f64": ("<d", 8),
}


class PointerResolveError(RuntimeError):
    """Pointer chain failed — the target may be stale."""


@dataclass
class ResolvedRecipe:
    recipe_id: str
    revision: int
    final_address: int
    value: Any
    value_type: str
    intermediate_addresses: list[int]


def revalidate_identity(recipe: Recipe, *, target_fingerprint: str) -> None:
    """Refuse to resolve a recipe that was authored on a different build."""
    if recipe.verification_status == 'stale':
        raise PointerResolveError('recipe is stale; revise and revalidate before use')
    if target_fingerprint != recipe.target_fingerprint:
        raise PointerResolveError(
            f"recipe {recipe.id} bound to target_fingerprint "
            f"{recipe.target_fingerprint}; live target is {target_fingerprint}"
        )
    # Absolute roots carry an implicit process-bound invariant: they are
    # only valid while the original process is alive. The caller is
    # expected to catch MemoryEngineError when reading through such a
    # root after the process exits.


def resolve_recipe(recipe: Recipe, *, adapter: Any,
                   module_base_lookup: Callable[[str], int],
                   target_fingerprint: str, process_instance_id: str | None = None) -> ResolvedRecipe:
    """Follow ``recipe.root`` + ``recipe.steps`` to the live value.

    ``module_base_lookup`` maps a module basename (e.g.
    ``"UnityFramework"``) to its current load address. Supplied by the
    target identity report from :mod:`targets`.
    """
    revalidate_identity(recipe, target_fingerprint=target_fingerprint)

    parsed = AddressRepr.from_str(recipe.root)
    kind = parsed.kind
    identity = getattr(adapter, 'identity', None)
    live_instance = (f'{identity.pid}:{identity.start_time_ns}' if identity is not None else process_instance_id)
    bindings = [p.split(':', 1)[1] for p in recipe.preconditions if p.startswith('process_instance:')]
    if bindings and (len(set(bindings)) != 1 or bindings[0] != live_instance):
        raise PointerResolveError('process identity differs from saved recipe')
    if kind == 'absolute' and not bindings:
        raise PointerResolveError('absolute recipe requires a process_instance binding')
    if kind in ("null", "placeholder"):
        raise PointerResolveError(f"recipe {recipe.id} root is {kind!r}; cannot resolve")
    if kind == "module_rel":
        name = parsed.module
        rva = parsed.value or 0
        try:
            module_base = module_base_lookup(name)
        except Exception as exc:
            raise PointerResolveError(
                f"module {name!r} not loaded (for recipe {recipe.id}): {exc}"
            ) from exc
        if type(module_base) is not int or not 0 < module_base < 2**64:
            raise PointerResolveError('module base is missing or invalid')
        addr = module_base + rva
    elif kind == "absolute":
        addr = parsed.value or 0
    elif kind == "zero_offset":
        addr = 0
    else:
        raise PointerResolveError(f"unknown root kind {kind!r}")

    intermediate = [addr]
    for step in recipe.steps:
        if not 0 < addr < 2**64:
            raise PointerResolveError('null or out-of-range pointer')
        op = step["op"]
        offset = int(step["offset"], 16)
        width = step["width"]
        if op == "add":
            addr = addr + offset
        elif op == "deref":
            if width not in (4, 8):
                raise PointerResolveError('invalid pointer width')
            fmt, size = ("<Q", 8) if width == 8 else ("<I", 4)
            raw = adapter.read_bytes(addr, size)
            if len(raw) != size:
                raise PointerResolveError(
                    f"short read at 0x{addr:x} (wanted {size}, got {len(raw)}): recipe {recipe.id}"
                )
            pointer = struct.unpack(fmt, raw)[0]
            if pointer == 0:
                raise PointerResolveError('null pointer')
            addr = pointer + offset
        else:
            raise PointerResolveError(f"unknown step op {op!r}")
        intermediate.append(addr)

    vt = recipe.value_type
    if not 0 < addr < 2**64:
        raise PointerResolveError('null or out-of-range final address')
    if vt == "bytes":
        # Allow reading arbitrary byte blobs via a 'size' precondition encoded as "size:N".
        size = 4
        for p in recipe.preconditions:
            if p.startswith("size:"):
                size = int(p.split(":", 1)[1])
                break
        if not 1 <= size <= 4 * 1024 * 1024:
            raise PointerResolveError('invalid byte read size')
        value = adapter.read_bytes(addr, size)
        if len(value) != size:
            raise PointerResolveError('short byte read')
    else:
        fmt, size = _SCALAR_FMT[vt]
        raw = adapter.read_bytes(addr, size)
        if len(raw) != size:
            raise PointerResolveError(
                f"final read short at 0x{addr:x}: got {len(raw)}/{size}"
            )
        value = struct.unpack(fmt, raw)[0]
        if isinstance(value, float) and not math.isfinite(value):
            raise PointerResolveError('nonfinite scalar value')
    if 'kill_timer_within_0_to_120_seconds' in recipe.preconditions and not 0 <= value <= 120:
        raise PointerResolveError('kill timer outside validated range')

    return ResolvedRecipe(
        recipe_id=recipe.id, revision=recipe.revision,
        final_address=addr, value=value,
        value_type=vt, intermediate_addresses=intermediate,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass
class PointerPath:
    """A candidate pointer chain discovered against the live target.

    The entry point is a module-relative or absolute address; the
    ``steps`` list describes a Cheat-Engine-style ``add/deref`` sequence
    that terminates at the known value.
    """
    root: str  # Serialised AddressRepr string
    steps: list[dict]  # [{'op': 'add'|'deref', 'offset': str, 'width': int}, ...]
    depth: int


class PointerPaths(list):
    """Candidates plus coverage; a capped/partial search is never exhaustive."""
    def __init__(self):
        super().__init__()
        self.complete = True
        self.skipped_regions = []
        self.reason = ""


def discover_pointer_paths(adapter, *, target_address: int, depth: int = 6,
                           offset_window: int = 4096, module_ranges=None,
                           max_depth: int = 12, max_candidates: int = 1024,
                           regions=None, cancel=None, deadline_s: float = 120.0):
    from .scans import ScanSession
    if not 1 <= depth <= min(max_depth, 12):
        raise ValueError("depth must be 1..12 and <= max_depth")
    if not 0 <= offset_window <= 4096 or not 1 <= max_candidates <= 65536:
        raise ValueError("invalid pointer search bounds")
    if not 0 < target_address < 2**64 or not 0 < deadline_s <= 120:
        raise ValueError("invalid target or time budget")
    regions = list(adapter.regions_writable() if regions is None else regions)
    modules = module_ranges or []
    found = PointerPaths()
    deadline = time.monotonic() + deadline_s
    current = {target_address: []}
    seen = set()
    absolute_paths = {}
    scanner = ScanSession(adapter, 'u64')
    for level in range(depth):
        following = {}
        for base, chunk, chunk_base in scanner._iter_chunks(regions, overlap=7):
            for offset in range((base - chunk_base) % 8, len(chunk)-7, 8):
                if offset % 4096 < 8:
                    if (cancel and cancel()) or time.monotonic() >= deadline:
                        found.complete = False
                        found.reason = 'cancelled' if cancel and cancel() else 'time_budget_exhausted'
                        found.skipped_regions = scanner.skipped_regions
                        return found
                pointer = struct.unpack_from('<Q', chunk, offset)[0]
                if pointer == 0:
                    continue
                for target, prior in current.items():
                    delta = target - pointer
                    if abs(delta) > offset_window:
                        continue
                    source = chunk_base + offset
                    steps = [{'op': 'deref', 'offset': hex(delta), 'width': 8}] + prior
                    key = (source, tuple((s['op'], s['offset']) for s in steps))
                    if key in seen:
                        continue
                    seen.add(key)
                    root = next((f'mod:{name}+0x{source-start:x}' for name, start, size in modules
                                 if start <= source and source+8 <= start+size), None)
                    if root is not None:
                        found.append(PointerPath(root, steps, level+1))
                    else:
                        following.setdefault(source, steps)
                        absolute_paths.setdefault(key, (source, steps))
                    if len(found) >= max_candidates or len(following) >= 65536 or len(seen) >= 131072 or len(absolute_paths)>=65536:
                        found.complete = False
                        found.reason = 'candidate_budget_exhausted'
                        found.skipped_regions = scanner.skipped_regions
                        return found
        current = following
        if not current:
            break
    for address, steps in absolute_paths.values():
        if len(found) >= max_candidates:
            found.complete = False
            found.reason = 'candidate_budget_exhausted'
            break
        if steps:
            found.append(PointerPath(f'abs:0x{address:x}', steps, len(steps)))
    found.skipped_regions = scanner.skipped_regions
    if found.skipped_regions:
        found.complete = False
        found.reason = 'partial_read'
    return found


__all__ = ['resolve_recipe', 'ResolvedRecipe', 'revalidate_identity',
           'discover_pointer_paths', 'PointerPath', 'PointerPaths', 'PointerResolveError']
