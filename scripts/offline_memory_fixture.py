#!/usr/bin/env python3
"""Disposable own-process memory target for native bridge testing.

Run this in a separate terminal and attach the memory workspace to its PID.
No game integration, anti-cheat bypass, external process changes, or network I/O.
"""
from __future__ import annotations
import ctypes
import os


class TestEntity(ctypes.Structure):
    _fields_ = [('health', ctypes.c_int32), ('armor', ctypes.c_int32),
                ('pos_x', ctypes.c_float), ('pos_y', ctypes.c_float), ('pos_z', ctypes.c_float)]


if __name__ == '__main__':
    entity = TestEntity(100, 50, 12.5, 25.5, 3.0)
    entity_ptr = ctypes.c_void_p(ctypes.addressof(entity))
    # Stable logical *pointer chain* within this process, not a fixed numeric
    # address across launches. ASLR may relocate the base on every restart.
    base = ctypes.addressof(entity_ptr)
    print('Own-process test target running. No anti-cheat or game dependency.', flush=True)
    print(f'PID: {os.getpid()}')
    print(f'Entity pointer base: 0x{base:x}')
    print(f'Health address (for verification): 0x{ctypes.addressof(entity):x}')
    print('Pointer chain: base=[entity pointer], offsets=[0] -> health (i32)')
    print('Commands: hit, heal, move, status, quit', flush=True)
    while True:
        try: command = input('fixture> ').strip().lower()
        except (EOFError, KeyboardInterrupt): break
        if command == 'hit': entity.health = max(0, entity.health-10)
        elif command == 'heal': entity.health = min(100, entity.health+10)
        elif command == 'move': entity.pos_x += 1; entity.pos_y += 0.5
        elif command in ('quit','exit'): break
        elif command not in ('status',''): print('Try: hit, heal, move, status, quit')
        print(f'Health={entity.health}, armor={entity.armor}, xyz=({entity.pos_x:.1f},{entity.pos_y:.1f},{entity.pos_z:.1f})', flush=True)
