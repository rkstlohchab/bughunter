"""Disposable target process holding an IL2CPP-shaped image in real heap memory.

Prints one JSON line with the measured buffer addresses, then decrements an
instance field every 100 ms until stdin closes. It is a test target, not a game.
"""
import ctypes
import json
import struct
import sys
import threading
import time

meta = ctypes.create_string_buffer(0x2000)
heap = ctypes.create_string_buffer(0x10000)
META, HEAP = ctypes.addressof(meta), ctypes.addressof(heap)


def put(buffer, base, address, raw):
    ctypes.memmove(base + (address - base), raw, len(raw))


names = b"\0Game.Combat\0PlayerWeapon\0_chargedAmmoLeft\0"
put(meta, META, META + 0x100, names)
at = {n: META + 0x100 + names.index(b"\0" + n + b"\0") + 1 for n in (b"Game.Combat", b"PlayerWeapon", b"_chargedAmmoLeft")}
weapon = HEAP + 0x1000
put(heap, HEAP, weapon + 0x10, struct.pack("<QQ", at[b"PlayerWeapon"], at[b"Game.Combat"]))
put(heap, HEAP, weapon + 0x40, struct.pack("<Q", weapon))
instances = [HEAP + 0x8000, HEAP + 0x8100]
for obj in instances:
    put(heap, HEAP, obj, struct.pack("<Q", weapon))
    put(heap, HEAP, obj + 0x24, struct.pack("<i", 30))
counter = ctypes.c_int32.from_address(instances[0] + 0x24)

print(json.dumps({"meta": META, "heap": HEAP, "weapon": weapon, "instances": instances}), flush=True)
stop = threading.Event()


def tick():
    while not stop.wait(0.1):
        counter.value = counter.value - 1 if counter.value > 0 else 30


threading.Thread(target=tick, daemon=True).start()
sys.stdin.read()
stop.set()
time.sleep(0.05)
