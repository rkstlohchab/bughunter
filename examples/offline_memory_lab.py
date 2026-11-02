#!/usr/bin/env python3
"""Disposable offline game data fixture for BugHunter memory / ESP testing.

No network or third-party processes. Numbers and the entity layout are intentionally
published so tests can verify genuine reads without inventing offsets. Addresses
change between launches. No anti-cheat/protection bypass is attempted.
"""
import ctypes
import json
import math
import os
import sys
import threading
import time


class Entity(ctypes.Structure):
    _fields_ = [('x', ctypes.c_float), ('y', ctypes.c_float),
                ('health', ctypes.c_int32), ('team', ctypes.c_int32),
                ('visible', ctypes.c_int32), ('id', ctypes.c_int32)]


def main():
    money = ctypes.c_int32(13371337)
    health = ctypes.c_int32(100)
    hp_pointer = ctypes.c_uint64(ctypes.addressof(health))
    entities = (Entity * 4)(
        Entity(-45, 12, 100, 1, 1, 1),
        Entity(18, 32, 80, 2, 1, 2),
        Entity(60, -10, 75, 2, 1, 3),
        Entity(-15, -55, 90, 1, 0, 4),
    )
    layout = {'count': len(entities), 'stride': ctypes.sizeof(Entity), 'fields': [
        {'name': n, 'type': ('f32' if n in ('x', 'y') else 'i32'),
         'offset': getattr(Entity, n).offset} for n in ('x', 'y', 'health', 'team', 'visible', 'id')]}
    stop = threading.Event()

    def animate():
        step = 0
        while not stop.wait(0.1):
            step += 1
            for i, e in enumerate(entities):
                e.x = float([-45, 18, 60, -15][i] + 7 * math.sin(step / 13 + i))
                e.y = float([12, 32, -10, -55][i] + 5 * math.cos(step / 12 + i))

    runner = threading.Thread(target=animate, daemon=True)
    runner.start()

    def state():
        return {'money': money.value, 'health': health.value,
                'entities': [dict(id=e.id, team=e.team, x=round(e.x, 2), y=round(e.y, 2),
                                  health=e.health, visible=e.visible) for e in entities]}

    print(json.dumps({'pid': os.getpid(), 'address': hex(ctypes.addressof(money)),
                      'type': 'i32', 'money': money.value,
                      'health_address': hex(ctypes.addressof(health)),
                      'health_pointer_slot': hex(ctypes.addressof(hp_pointer)),
                      'entity_base': hex(ctypes.addressof(entities)),
                      'entity_layout': layout,
                      'note': 'Lab-only addresses. Re-read on each launch; not stable pointers.'}), flush=True)
    try:
        for line in sys.stdin:
            cmd = line.strip().split()
            if not cmd:
                continue
            if cmd[0] == 'status':
                print(json.dumps(state()), flush=True)
            elif cmd[0] == 'earn' and len(cmd) == 2:
                try:
                    money.value += int(cmd[1]); print(json.dumps(state()), flush=True)
                except ValueError:
                    print(json.dumps({'error': 'enter an integer'}), flush=True)
            elif cmd[0] == 'damage' and len(cmd) == 2:
                try:
                    health.value = max(0, health.value - int(cmd[1]));print(json.dumps(state()), flush=True)
                except ValueError:
                    print(json.dumps({'error': 'enter an integer'}), flush=True)
            elif cmd[0] == 'heal' and len(cmd) == 2:
                try:
                    health.value = min(1000, health.value + int(cmd[1])); print(json.dumps(state()), flush=True)
                except ValueError:
                    print(json.dumps({'error': 'enter an integer'}), flush=True)
            elif cmd[0] == 'quit':
                print(json.dumps({'stopped': True}), flush=True);break
            else:
                print(json.dumps({'commands': ['status', 'earn 10', 'damage 10', 'heal 10', 'quit']}), flush=True)
    finally:
        stop.set()
        runner.join(timeout=1)


if __name__ == '__main__':
    main()
