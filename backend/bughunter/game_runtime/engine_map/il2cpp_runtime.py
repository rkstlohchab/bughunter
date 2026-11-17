"""IL2CPP runtime structures located by evidence, not by version tables.

Class records: IL2CPP keeps each class name as a pointer into the loaded
identifier strings. For every pointer to ``\\0<Name>\\0`` the probe tests
record bases at 8-byte steps before the pointer and keeps the base that the
rest of memory references most (objects, parents and type tables point at a
class base, not into its middle). Self-references inside the candidate
record are reported separately as supporting evidence. The derived name-slot
offset is a measurement for this process and build, never a constant.

Instances: an object whose first qword equals the class address. That rule
is the assumption this probe tests, so instances stay ``candidate`` until
one is confirmed independently (for example a value-scan hit whose
enclosing object header equals the class address).

Fields: pointers to ``\\0<field>\\0`` whose neighbourhood also holds the
owning class address are field-record candidates; small int32 values next
to them are reported as instance-offset candidates for confirmation with
dissect/watch on a live instance.
"""
from __future__ import annotations

import struct

from .memview import (
    Sweep,
    contains,
    is_identifier,
    qwords,
    read_cstring,
    read_u64,
    safe_read,
)
from .probe import Probe, check_name, round8

BASE_WINDOW = 0x100          # name slot searched at 0..0xF8 from the record base
RECORD_WINDOW = 0x200        # bytes read to inspect a candidate record
MAX_STRINGS = 64
MAX_REFS = 256
MAX_INSTANCES = 512


def _find_all(raw: bytes, pattern: bytes, address: int, out: list[int], cap: int, shift: int) -> None:
    start = 0
    while len(out) < cap:
        hit = raw.find(pattern, start)
        if hit < 0:
            return
        value = address + hit + shift
        if value not in out:
            out.append(value)
        start = hit + 1


def _collect_refs(raw: bytes, address: int, targets: set[int], out: list[tuple[int, int]], cap: int) -> None:
    values = qwords(raw)
    hits = targets.intersection(values)
    if not hits:
        return
    for index, value in enumerate(values):
        if value in hits and len(out) < cap:
            out.append((address + index * 8, value))


class _StringRefs(Probe):
    """Phases 1-2 shared by class and field probes: string copies, then pointers to them."""

    def __init__(self, name: str, readable, writable):
        super().__init__()
        self.name = check_name(name)
        self.readable, self.writable = readable, writable
        self.strings: list[int] = []
        self.refs: list[tuple[int, int]] = []
        self.phase, self.sweep = "strings", Sweep(list(readable))

    def _strings_refs(self, reader, stop) -> bool:
        if self.phase == "strings":
            pattern = b"\0" + self.name + b"\0"
            done = self.sweep.advance(reader, lambda raw, at: _find_all(raw, pattern, at, self.strings, MAX_STRINGS, 1),
                                      overlap=round8(len(pattern)), stop=stop)
            if not done:
                return False
            if not self.strings:
                self.notes.append("name string not found in readable memory; check spelling or wait until the type is loaded")
                self.result = {"status": "not_found", "strings": [], "candidates": []}
                self.phase = "done"
                return True
            self.phase, self.sweep = "refs", Sweep(list(self.writable))
            return True
        if self.phase == "refs":
            targets = set(self.strings)
            done = self.sweep.advance(reader, lambda raw, at: _collect_refs(raw, at, targets, self.refs, MAX_REFS), stop=stop)
            if not done:
                return False
            if len(self.refs) >= MAX_REFS:
                self.notes.append(f"pointer references capped at {MAX_REFS}")
            if not self.refs:
                self.notes.append("no writable pointer references the name; the type may not be initialised yet")
                self.result = {"status": "not_found", "strings": [hex(s) for s in self.strings], "candidates": []}
                self.phase = "done"
            else:
                self._after_refs()
            return True
        return False

    def _after_refs(self):  # pragma: no cover - interface
        raise NotImplementedError


class Il2CppClassProbe(_StringRefs):
    kind = "il2cpp_class"

    def __init__(self, name: str, readable, writable, namespace: str | None = None):
        super().__init__(name, readable, writable)
        self.namespace = None if namespace is None else namespace.encode("ascii")
        self.bases: dict[int, list[tuple[int, int]]] = {}
        self.inbound: dict[int, int] = {}
        self.referrers: dict[int, list[int]] = {}

    def _after_refs(self):
        for slot, _ in self.refs:
            for delta in range(0, BASE_WINDOW, 8):
                base = slot - delta
                if base > 0:
                    self.bases.setdefault(base, []).append((slot, delta))
        self.phase, self.sweep = "bases", Sweep(list(self.writable))

    def _count(self, raw: bytes, address: int) -> None:
        values = qwords(raw)
        hits = set(self.bases).intersection(values)
        for base in hits:
            self.inbound[base] = self.inbound.get(base, 0) + values.count(base)
            seen = self.referrers.setdefault(base, [])
            if len(seen) < 4:
                seen.append(address + values.index(base) * 8)

    def _step(self, reader, stop) -> bool:
        if self.phase in ("strings", "refs"):
            return self._strings_refs(reader, stop)
        if self.phase == "bases":
            if not self.sweep.advance(reader, self._count, stop=stop):
                return False
            self._finalize(reader)
            return True
        return False

    def _finalize(self, reader) -> None:
        candidates = []
        for slot, string_address in self.refs:
            options = []
            for delta in range(0, BASE_WINDOW, 8):
                base = slot - delta
                total = self.inbound.get(base, 0)
                if total:
                    options.append((base, delta, total))
            if not options:
                continue
            record = {}
            scored = []
            window_start = slot - BASE_WINDOW
            window = safe_read(reader, window_start, BASE_WINDOW + RECORD_WINDOW) or b""
            for base, delta, total in options:
                if len(window) == BASE_WINDOW + RECORD_WINDOW:
                    raw = window[base - window_start:base - window_start + RECORD_WINDOW]
                else:
                    raw = safe_read(reader, base, RECORD_WINDOW) or b""
                values = qwords(raw)
                self_refs = values.count(base) if values else 0
                external = max(0, total - self_refs)
                record[base] = values
                scored.append((external, self_refs, base, delta))
            scored.sort(reverse=True)
            external, self_refs, base, delta = scored[0]
            if external < 1:
                continue
            namespace, ns_delta = None, None
            for neighbour in (8, -8):
                pointer = read_u64(reader, slot + neighbour)
                if pointer and pointer != string_address:
                    text = read_cstring(reader, pointer)
                    if is_identifier(text, allow_empty=True):
                        namespace, ns_delta = text.decode("ascii"), neighbour
                        break
            if self.namespace is not None and (namespace is None or namespace.encode("ascii") != self.namespace):
                continue
            values = record.get(base) or []
            pointers = []
            for index, value in enumerate(values):
                if value and value != base and contains(self.writable, value, 8):
                    pointers.append({"slot_offset": hex(index * 8), "points_to": hex(value)})
            confidence = "candidate" if (self_refs >= 1 or external >= 2) and namespace is not None else "weak"
            candidates.append({
                "class_address": hex(base), "name_slot_offset": hex(delta),
                "name_pointer_slot": hex(slot), "name_string": hex(string_address),
                "namespace": namespace, "namespace_slot_delta": ns_delta,
                "external_references": external, "self_references": self_refs,
                "referrers": [hex(a) for a in self.referrers.get(base, [])],
                "alternatives": [{"class_address": hex(b), "name_slot_offset": hex(d), "external_references": e}
                                 for e, _, b, d in scored[1:3]],
                "record_pointers": pointers[:40], "confidence": confidence,
            })
        candidates.sort(key=lambda c: (c["confidence"] != "candidate", -c["external_references"]))
        offsets = {c["name_slot_offset"] for c in candidates if c["confidence"] == "candidate"}
        if len(offsets) > 1:
            self.notes.append("candidates disagree on the name-slot offset; treat the minority as unrelated records")
        self.result = {"status": "found" if candidates else "not_found", "name": self.name.decode(),
                       "strings": [hex(s) for s in self.strings], "references": len(self.refs),
                       "candidates": candidates[:16]}
        label = self.name.decode()
        for c in candidates[:4]:
            if c["confidence"] != "candidate":
                continue
            qualified = f"{c['namespace']}.{label}" if c["namespace"] else label
            self.findings.append({
                "kind": "class_layout", "title": f"IL2CPP class {qualified}",
                "address": "abs:" + c["class_address"], "value_type": None,
                "evidence": {"name_slot_offset": c["name_slot_offset"], "namespace_slot_delta": c["namespace_slot_delta"],
                             "external_references": c["external_references"], "self_references": c["self_references"],
                             "rule": "record base with the most external pointers among bases 0..0xF8 before the name pointer"}})
        self.phase = "done"


class Il2CppInstanceProbe(Probe):
    kind = "il2cpp_instances"

    def __init__(self, class_address: int, writable):
        super().__init__()
        if not 0 < class_address < 2**64:
            raise ValueError("class_address must be a measured nonzero address")
        self.class_address = class_address
        self.instances: list[int] = []
        self.total = 0
        self.phase, self.sweep = "instances", Sweep(list(writable))

    def _visit(self, raw: bytes, address: int) -> None:
        values = qwords(raw)
        count = values.count(self.class_address)
        if not count:
            return
        start = 0
        for _ in range(count):
            index = values.index(self.class_address, start)
            start = index + 1
            slot = address + index * 8
            if self.class_address <= slot < self.class_address + RECORD_WINDOW:
                continue
            self.total += 1
            if len(self.instances) < MAX_INSTANCES:
                self.instances.append(slot)

    def _step(self, reader, stop) -> bool:
        if not self.sweep.advance(reader, self._visit, stop=stop):
            return False
        self.result = {"status": "found" if self.instances else "not_found",
                       "class_address": hex(self.class_address), "total": self.total,
                       "instances": [hex(a) for a in self.instances[:64]],
                       "rule": "object header qword equals class address (unconfirmed until an independent hit matches)"}
        if self.total > len(self.instances):
            self.notes.append(f"instances capped at {MAX_INSTANCES}")
        self.phase = "done"
        return True


class Il2CppFieldProbe(_StringRefs):
    kind = "il2cpp_field"

    def __init__(self, name: str, class_address: int, readable, writable):
        super().__init__(name, readable, writable)
        if not 0 < class_address < 2**64:
            raise ValueError("class_address must be a measured nonzero address")
        self.class_address = class_address

    def _after_refs(self):
        self.phase = "fields"

    def _step(self, reader, stop) -> bool:
        if self.phase in ("strings", "refs"):
            return self._strings_refs(reader, stop)
        if self.phase != "fields":
            return False
        candidates = []
        for slot, string_address in self.refs:
            start = slot - 0x40
            raw = safe_read(reader, start, 0x80)
            if raw is None:
                continue
            values = qwords(raw)
            parents = [index * 8 + start - slot for index, value in enumerate(values) if value == self.class_address]
            if not parents:
                continue
            offsets = []
            for position in range(0, len(raw), 4):
                value = struct.unpack_from("<i", raw, position)[0]
                delta = start + position - slot
                if 0x10 <= value <= 0x4000 and delta not in parents and not any(p <= delta < p + 8 for p in parents):
                    offsets.append({"slot_delta": delta, "value": hex(value)})
            candidates.append({"field_name_slot": hex(slot), "name_string": hex(string_address),
                               "parent_slot_deltas": parents, "offset_candidates": offsets[:12]})
        self.result = {"status": "found" if candidates else "not_found", "name": self.name.decode(),
                       "class_address": hex(self.class_address), "candidates": candidates[:16],
                       "next": "dissect an instance at each offset candidate and watch it while the value changes in game"}
        for c in candidates[:4]:
            self.findings.append({"kind": "field", "title": f"IL2CPP field {self.name.decode()} record",
                                  "address": "abs:" + c["field_name_slot"], "value_type": None,
                                  "evidence": {"class_address": hex(self.class_address),
                                               "parent_slot_deltas": c["parent_slot_deltas"],
                                               "offset_candidates": c["offset_candidates"][:6]}})
        self.phase = "done"
        return True
