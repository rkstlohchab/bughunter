"""Sparse fake process memory for engine-map and live-analysis tests."""
from __future__ import annotations

import struct

from bughunter.memory_engine.protocol import ProcessIdentity


class FakeMemory:
    def __init__(self):
        self.blocks: dict[int, bytearray] = {}
        self.writable: set[int] = set()
        self.module_rows: list[dict] = []
        self.reads = 0
        self.identity = ProcessIdentity(pid=4242, start_time_ns=1, target_fingerprint="fp")

    def add(self, base: int, size: int, *, writable: bool = True) -> int:
        self.blocks[base] = bytearray(size)
        if writable:
            self.writable.add(base)
        return base

    def module(self, name: str, base: int, size: int, path: str | None = None):
        self.module_rows.append({"path": path or f"/Games/App/{name}", "base": hex(base), "size": size})

    def _locate(self, address: int, size: int):
        for base, data in self.blocks.items():
            if base <= address and address + size <= base + len(data):
                return data, address - base
        raise RuntimeError(f"unmapped 0x{address:x}+{size}")

    def read_bytes(self, address: int, size: int) -> bytes:
        self.reads += 1
        data, offset = self._locate(address, size)
        return bytes(data[offset:offset + size])

    def write(self, address: int, raw: bytes) -> None:
        data, offset = self._locate(address, len(raw))
        data[offset:offset + len(raw)] = raw

    def u64(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<Q", value))

    def u32(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<I", value))

    def i32(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<i", value))

    def f32(self, address: int, value: float) -> None:
        self.write(address, struct.pack("<f", value))

    def regions_writable(self):
        return [(b, len(self.blocks[b])) for b in sorted(self.writable)]

    def regions(self, writable_only: bool = False):
        bases = sorted(self.writable) if writable_only else sorted(self.blocks)
        return [(b, len(self.blocks[b])) for b in bases]

    def modules(self):
        return list(self.module_rows)

    def close(self):
        pass


def il2cpp_image():
    """Metadata strings, two class records, one field record, two instances and a decoy pointer."""
    mem = FakeMemory()
    meta = mem.add(0x1000_0000, 0x4000, writable=False)
    heap = mem.add(0x2000_0000, 0x10000)
    names = b"\0Game.Combat\0PlayerWeapon\0MonoBehaviour\0UnityEngine\0_chargedAmmoLeft\0"
    mem.write(meta + 0x100, names)
    at = {n: meta + 0x100 + names.index(b"\0" + n + b"\0") + 1
          for n in (b"Game.Combat", b"PlayerWeapon", b"MonoBehaviour", b"UnityEngine", b"_chargedAmmoLeft")}
    weapon, behaviour = heap + 0x1000, heap + 0x3000
    for klass, name, namespace in ((weapon, b"PlayerWeapon", b"Game.Combat"), (behaviour, b"MonoBehaviour", b"UnityEngine")):
        mem.u64(klass + 0x10, at[name])
        mem.u64(klass + 0x18, at[namespace])
        mem.u64(klass + 0x40, klass)          # self reference inside the record
    mem.u64(weapon + 0x58, behaviour)         # parent
    mem.u64(heap + 0x6000, behaviour)         # external reference to the parent
    field = heap + 0x5000
    mem.u64(field, at[b"_chargedAmmoLeft"])
    mem.u64(field + 0x10, weapon)             # owning class
    mem.u32(field + 0x18, 0x24)               # instance offset
    instances = [heap + 0x8000, heap + 0x8100]
    for index, obj in enumerate(instances):
        mem.u64(obj, weapon)
        mem.i32(obj + 0x24, 30 - index)
    mem.u64(heap + 0x9000, at[b"PlayerWeapon"])  # decoy: lone pointer to the name, nothing references its bases
    return mem, {"weapon": weapon, "behaviour": behaviour, "field": field, "instances": instances, "strings": at}


def _entry(name: bytes) -> bytes:
    return struct.pack("<H", len(name) << 6) + name


def unreal_image():
    """Name block 0, a block table inside a module, Class/package objects and an object array."""
    mem = FakeMemory()
    module = mem.add(0x4000_0000, 0x2000)
    mem.module("Game-Mac-Shipping", module, 0x2000)
    names_region = mem.add(0x5000_0000, 0x4000, writable=False)
    heap = mem.add(0x6000_0000, 0x20000)
    names = [b"None", b"ByteProperty", b"IntProperty", b"BoolProperty", b"FloatProperty", b"ObjectProperty",
             b"Class", b"/Script/CoreUObject", b"Object", b"Package", b"Actor", b"Pawn", b"Weapon",
             b"Character", b"Controller", b"World", b"Level", b"Function"]
    block = names_region + 0x200
    cursor, index = block, {}
    for name in names:
        index[name] = (cursor - block) // 2
        raw = _entry(name)
        mem.write(cursor, raw)
        cursor += len(raw) + (len(raw) % 2)
    mem.u64(module + 0x800, block)           # block table slot 0
    class_object, package = heap + 0x1000, heap + 0x1100
    mem.u64(class_object + 0x10, class_object)
    mem.u32(class_object + 0x18, index[b"Class"])
    mem.u64(class_object + 0x20, package)
    mem.u64(package + 0x10, class_object)
    mem.u32(package + 0x18, index[b"/Script/CoreUObject"])
    others = []
    for n, name in enumerate([b"Object", b"Package", b"Actor", b"Pawn", b"Weapon", b"Character", b"Controller",
                              b"World", b"Level", b"Function", b"IntProperty", b"BoolProperty"]):
        obj = heap + 0x2000 + n * 0x100
        mem.u64(obj + 0x10, class_object)
        mem.u32(obj + 0x18, index[name])
        others.append(obj)
    array = heap + 0x10000
    items = others[:6] + [class_object] + [package] + others[6:]
    for n, obj in enumerate(items):
        mem.u64(array + n * 0x18, obj)
    return mem, {"block": block, "blocks_slot": module + 0x800, "class_object": class_object, "package": package,
                 "array_entry": array + 6 * 0x18, "module": module}
