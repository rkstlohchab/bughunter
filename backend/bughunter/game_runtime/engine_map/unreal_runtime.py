"""Unreal name pool and object layout derived from invariants in memory.

No Unreal build was available when this was written; everything here is
verified against synthetic images only and every result says so.

Names: the first entries of the first name block are ``None`` then
``ByteProperty``. The probe finds that pair, takes the byte gap between them
as the entry-header size, and solves for the header bit-field that encodes
both lengths (4 and 12). The solution is accepted only if walking the block
with it decodes a run of identifier-like names. Pointers to the block start
locate the block table slot (module-relative when it lies in a module).

Objects: the UClass object named ``Class`` has its class pointer equal to
itself. The probe sweeps for 8-aligned addresses A with ``*(A+c) == A`` for
small c, then solves for the name slot and the name-index encoding
(block shift, entry stride) that decode A's name to ``Class``. The outer
slot is confirmed when it leads to an object named ``/Script/CoreUObject``.
Object-array entries are then the slots that point at A with neighbours at a
fixed stride that also decode to names.
"""
from __future__ import annotations

import struct

from .memview import Sweep, is_identifier, qwords, read_u64, root_for, safe_read
from .probe import Probe

FIRST_NAMES = (b"None", b"ByteProperty")
MAX_OCCURRENCES = 32
MAX_SELF_CLASS = 256
MAX_ARRAY_HITS = 16
SELF_SLOTS = range(0x08, 0x38, 0x08)
NAME_SLOTS = range(0x00, 0x40, 0x04)
INDEX_SHIFTS = range(12, 21)
ENTRY_STRIDES = (1, 2, 4, 8)
ARRAY_STRIDES = (0x08, 0x10, 0x18, 0x20, 0x28, 0x30)
VERIFIED = "synthetic images only; not verified on an Unreal build"


def solve_header(hdr_none: int, hdr_byteprop: int, header_bytes: int) -> list[tuple[int, int]]:
    """All (shift, bits) bit-fields that read 4 from the first header and 12 from the second."""
    out = []
    total = header_bytes * 8
    for bits in range(4, total + 1):
        mask = (1 << bits) - 1
        for shift in range(total - bits + 1):
            if (hdr_none >> shift) & mask == 4 and (hdr_byteprop >> shift) & mask == 12:
                out.append((shift, bits))
    return out


def walk_block(blob: bytes, header_bytes: int, shift: int, bits: int, align: int, limit: int = 64) -> list[str]:
    names, cursor, mask = [], 0, (1 << bits) - 1
    while len(names) < limit and cursor + header_bytes <= len(blob):
        header = int.from_bytes(blob[cursor:cursor + header_bytes], "little")
        length = (header >> shift) & mask
        if not 1 <= length <= 1024 or cursor + header_bytes + length > len(blob):
            break
        text = blob[cursor + header_bytes:cursor + header_bytes + length]
        if not is_identifier(text) and not text.startswith(b"/"):
            break
        names.append(text.decode("ascii"))
        cursor += header_bytes + length
        cursor += (-cursor) % align
    return names


class NameLayout:
    def __init__(self, blocks_slot: int, header_bytes: int, len_shift: int, len_bits: int, align: int):
        self.blocks_slot, self.header_bytes = blocks_slot, header_bytes
        self.len_shift, self.len_bits, self.align = len_shift, len_bits, align

    @classmethod
    def from_wire(cls, value: dict) -> NameLayout:
        return cls(int(value["blocks_slot"], 16), int(value["header_bytes"]), int(value["len_shift"]),
                   int(value["len_bits"]), int(value["align"]))

    def decode(self, reader, index: int, block_shift: int, stride: int, cache: dict) -> str | None:
        block, offset = index >> block_shift, (index & ((1 << block_shift) - 1)) * stride
        if block > 8192:
            return None
        key = (block_shift, stride, index)
        if key in cache:
            return cache[key]
        base = cache.get(("block", block))
        if base is None:
            base = read_u64(reader, self.blocks_slot + 8 * block) or 0
            cache[("block", block)] = base
        name = None
        if base:
            raw = safe_read(reader, base + offset, self.header_bytes + 64)
            if raw is not None:
                header = int.from_bytes(raw[:self.header_bytes], "little")
                length = (header >> self.len_shift) & ((1 << self.len_bits) - 1)
                text = raw[self.header_bytes:self.header_bytes + length]
                if 1 <= length <= 64 and len(text) == length and (is_identifier(text) or text.startswith(b"/")):
                    name = text.decode("ascii")
        cache[key] = name
        return name


class UnrealNamesProbe(Probe):
    kind = "unreal_names"

    def __init__(self, readable, writable, modules):
        super().__init__()
        self.readable, self.writable, self.modules = readable, writable, modules
        self.occurrences: list[int] = []
        self.layout: dict | None = None
        self.block_refs: list[int] = []
        self.phase, self.sweep = "strings", Sweep(list(readable))

    def _find(self, raw: bytes, address: int) -> None:
        start = 0
        while len(self.occurrences) < MAX_OCCURRENCES:
            hit = raw.find(FIRST_NAMES[1], start)
            if hit < 0:
                return
            if address + hit not in self.occurrences:
                self.occurrences.append(address + hit)
            start = hit + 1

    def _derive(self, reader) -> dict | None:
        for occurrence in self.occurrences:
            before = safe_read(reader, occurrence - 48, 48)
            if before is None:
                continue
            at = before.rfind(FIRST_NAMES[0])
            if at < 0:
                continue
            gap = 48 - (at + 4)
            if not 1 <= gap <= 8 or at - gap < 0:
                continue
            hdr_none = int.from_bytes(before[at - gap:at], "little")
            hdr_byte = int.from_bytes(before[48 - gap:], "little")
            block_start = occurrence - 48 + at - gap
            blob = safe_read(reader, block_start, 8192)
            if blob is None:
                continue
            best = None
            for shift, bits in solve_header(hdr_none, hdr_byte, gap):
                for align in (2, 1, 4, 8):
                    names = walk_block(blob, gap, shift, bits, align)
                    if names[:2] == ["None", "ByteProperty"] and (best is None or len(names) > len(best[0])):
                        best = (names, shift, bits, align)
            if best and len(best[0]) >= 8:
                names, shift, bits, align = best
                return {"block_start": block_start, "header_bytes": gap, "len_shift": shift, "len_bits": bits,
                        "align": align, "decoded_names": names[:24], "decoded_count": len(names)}
        return None

    def _step(self, reader, stop) -> bool:
        if self.phase == "strings":
            if not self.sweep.advance(reader, self._find, overlap=16, stop=stop):
                return False
            self.layout = self._derive(reader)
            if self.layout is None:
                self.notes.append("no None/ByteProperty pair decoded; older or customised name layout, or a wide-character pool")
                self.result = {"status": "not_found", "occurrences": [hex(o) for o in self.occurrences], "verified": VERIFIED}
                self.phase = "done"
                return True
            self.phase, self.sweep = "block_refs", Sweep(list(self.writable))
            return True
        if self.phase == "block_refs":
            target = self.layout["block_start"]

            def visit(raw, address):
                values = qwords(raw)
                start = 0
                for _ in range(values.count(target)):
                    index = values.index(target, start)
                    start = index + 1
                    if len(self.block_refs) < 32:
                        self.block_refs.append(address + index * 8)

            if not self.sweep.advance(reader, visit, stop=stop):
                return False
            slots = []
            for slot in self.block_refs:
                slots.append({"blocks_slot": hex(slot), "root": root_for(self.modules, slot)})
            layout = {**self.layout, "block_start": hex(self.layout["block_start"])}
            self.result = {"status": "found", "layout": layout, "block_table_slots": slots, "verified": VERIFIED,
                           "next": "pass one blocks_slot with this layout to the unreal_objects probe"}
            self.findings.append({"kind": "name_pool", "title": "Unreal name block 0",
                                  "address": f"abs:0x{self.layout['block_start']:x}", "value_type": None,
                                  "evidence": {k: layout[k] for k in ("header_bytes", "len_shift", "len_bits", "align", "decoded_count")}})
            for row in slots[:4]:
                if row["root"].startswith("mod:"):
                    self.findings.append({"kind": "static_root", "title": "Unreal name block table slot",
                                          "address": row["root"], "value_type": "u64",
                                          "evidence": {"points_to": layout["block_start"]}})
            self.phase = "done"
            return True
        return False


class UnrealObjectsProbe(Probe):
    kind = "unreal_objects"

    def __init__(self, layout: NameLayout, writable, modules):
        super().__init__()
        self.names, self.writable, self.modules = layout, writable, modules
        self.self_class: list[tuple[int, int]] = []
        self.object: dict | None = None
        self.array_hits: list[int] = []
        self.cache: dict = {}
        self.phase, self.sweep = "self_class", Sweep(list(writable))

    def _self(self, raw: bytes, address: int) -> None:
        values = qwords(raw)
        present = set(values)
        for slot in SELF_SLOTS:
            low = address - slot
            for candidate in present.intersection(range(low, low + 8 * len(values), 8)):
                index = (candidate + slot - address) // 8
                if 0 <= index < len(values) and values[index] == candidate and len(self.self_class) < MAX_SELF_CLASS:
                    self.self_class.append((candidate, slot))

    def _name_of(self, reader, obj: int, name_slot: int, shift: int, stride: int) -> str | None:
        raw = safe_read(reader, obj + name_slot, 4)
        if raw is None:
            return None
        return self.names.decode(reader, struct.unpack("<I", raw)[0], shift, stride, self.cache)

    def _derive(self, reader) -> dict | None:
        for obj, class_slot in self.self_class:
            head = safe_read(reader, obj, 0x40)
            if head is None:
                continue
            for name_slot in NAME_SLOTS:
                index = struct.unpack_from("<I", head, name_slot)[0]
                for shift in INDEX_SHIFTS:
                    for stride in ENTRY_STRIDES:
                        if self.names.decode(reader, index, shift, stride, self.cache) != "Class":
                            continue
                        for outer_slot in SELF_SLOTS:
                            outer = struct.unpack_from("<Q", head, outer_slot)[0]
                            if outer and outer != obj and self._name_of(reader, outer, name_slot, shift, stride) == "/Script/CoreUObject":
                                outer_index = struct.unpack("<I", safe_read(reader, outer + name_slot, 4) or b"\0\0\0\0")[0]
                                shifts = [s for s in INDEX_SHIFTS
                                          if self.names.decode(reader, index, s, stride, self.cache) == "Class"
                                          and self.names.decode(reader, outer_index, s, stride, self.cache) == "/Script/CoreUObject"]
                                if len(shifts) > 1:
                                    self.notes.append("index block shift undetermined: both names lie in name block 0; "
                                                      f"any of {shifts[0]}..{shifts[-1]} decodes them")
                                return {"class_object": obj, "class_slot": class_slot, "name_slot": name_slot,
                                        "outer_slot": outer_slot, "index_block_shift": shift if len(shifts) == 1 else None,
                                        "index_block_shift_candidates": shifts, "entry_stride": stride,
                                        "_decode_shift": shift}
        return None

    def _step(self, reader, stop) -> bool:
        if self.phase == "self_class":
            if not self.sweep.advance(reader, self._self, stop=stop):
                return False
            self.object = self._derive(reader)
            if self.object is None:
                self.notes.append("no self-referencing object decoded to Class with a /Script/CoreUObject outer")
                self.result = {"status": "not_found", "self_class_candidates": len(self.self_class), "verified": VERIFIED}
                self.phase = "done"
                return True
            self.phase, self.sweep = "array", Sweep(list(self.writable))
            return True
        if self.phase == "array":
            target = self.object["class_object"]

            def visit(raw, address):
                values = qwords(raw)
                start = 0
                for _ in range(values.count(target)):
                    index = values.index(target, start)
                    start = index + 1
                    slot = address + index * 8
                    if len(self.array_hits) < MAX_ARRAY_HITS and not target <= slot < target + 0x40:
                        self.array_hits.append(slot)

            if not self.sweep.advance(reader, visit, stop=stop):
                return False
            self._finalize(reader)
            return True
        return False

    def _finalize(self, reader) -> None:
        o = self.object
        arrays = []
        for hit in self.array_hits:
            window = safe_read(reader, hit - 0x200, 0x400)
            if window is None:
                continue
            best = (0, None)
            for stride in ARRAY_STRIDES:
                valid = 0
                for step in range(-8, 9):
                    if step == 0:
                        continue
                    at = 0x200 + step * stride
                    if not 0 <= at <= len(window) - 8:
                        continue
                    pointer = struct.unpack_from("<Q", window, at)[0]
                    if pointer and self._name_of(reader, pointer, o["name_slot"], o["_decode_shift"], o["entry_stride"]):
                        valid += 1
                if valid > best[0]:
                    best = (valid, stride)
            if best[0] >= 6:
                arrays.append({"entry": hex(hit), "item_stride": hex(best[1]), "named_neighbours": best[0]})
        layout = {"class_slot": hex(o["class_slot"]), "name_slot": hex(o["name_slot"]), "outer_slot": hex(o["outer_slot"]),
                  "index_block_shift": o["index_block_shift"],
                  "index_block_shift_candidates": o["index_block_shift_candidates"], "entry_stride": o["entry_stride"]}
        self.result = {"status": "found", "class_object": hex(o["class_object"]), "object_layout": layout,
                       "object_array_entries": arrays, "verified": VERIFIED,
                       "next": "game_pointer_search from an object_array entry finds the chunk table; dissect objects with this layout"}
        self.findings.append({"kind": "class_layout", "title": "Unreal UObject layout (Class object)",
                              "address": f"abs:0x{o['class_object']:x}", "value_type": None, "evidence": layout})
        for row in arrays[:2]:
            self.findings.append({"kind": "object_array", "title": "Unreal object array entry for Class",
                                  "address": "abs:" + row["entry"], "value_type": "u64",
                                  "evidence": {"item_stride": row["item_stride"], "named_neighbours": row["named_neighbours"]}})
        self.phase = "done"
