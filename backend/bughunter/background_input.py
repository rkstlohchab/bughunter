"""Deliver keyboard input to one application without focusing it (Windows/Linux).

macOS: Quartz ``CGEventPostToPid`` places events directly in the target process's
event queue. Windows: ``PostMessageW`` WM_KEYDOWN/WM_KEYUP/WM_CHAR to the
process's top-level windows. Linux/X11: ``xdotool key --window``. None of these
move the cursor or change which window has focus, so the user keeps working.

Limits are reported, not hidden: apps that read raw HID input, sandboxed/protected
apps, and Wayland sessions may ignore process-targeted events. The caller must
verify the effect (screenshot, accessibility read or memory read).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import platform
import shutil
import subprocess
import time

MAX_STEPS = 200
MAX_HOLD_MS = 5000

# macOS ANSI virtual key codes (Carbon kVK_*).
MAC_KEYS = {
    **{c: k for c, k in zip('asdfhgzxcv', (0, 1, 2, 3, 4, 5, 6, 7, 8, 9))},
    'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15, 'y': 16, 't': 17, '1': 18, '2': 19, '3': 20, '4': 21,
    '6': 22, '5': 23, '=': 24, '9': 25, '7': 26, '-': 27, '8': 28, '0': 29, ']': 30, 'o': 31, 'u': 32,
    '[': 33, 'i': 34, 'p': 35, 'l': 37, 'j': 38, "'": 39, 'k': 40, ';': 41, '\\': 42, ',': 43, '/': 44,
    'n': 45, 'm': 46, '.': 47, '`': 50,
    'return': 36, 'enter': 36, 'tab': 48, 'space': 49, 'delete': 51, 'backspace': 51, 'escape': 53, 'esc': 53,
    'left': 123, 'right': 124, 'down': 125, 'up': 126, 'home': 115, 'end': 119, 'pageup': 116, 'pagedown': 121,
    'f1': 122, 'f2': 120, 'f3': 99, 'f4': 118, 'f5': 96, 'f6': 97, 'f7': 98, 'f8': 100, 'f9': 101, 'f10': 109,
    'f11': 103, 'f12': 111,
}
MAC_FLAGS = {'shift': 1 << 17, 'ctrl': 1 << 18, 'control': 1 << 18, 'alt': 1 << 19, 'option': 1 << 19,
             'cmd': 1 << 20, 'command': 1 << 20}
# Windows virtual-key codes.
WIN_KEYS = {**{c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'},
            'return': 0x0D, 'enter': 0x0D, 'tab': 0x09, 'space': 0x20, 'backspace': 0x08, 'delete': 0x2E,
            'escape': 0x1B, 'esc': 0x1B, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
            'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'shift': 0x10, 'ctrl': 0x11,
            'control': 0x11, 'alt': 0x12, **{f'f{i}': 0x6F + i for i in range(1, 13)}}
X_KEYS = {'return': 'Return', 'enter': 'Return', 'tab': 'Tab', 'space': 'space', 'backspace': 'BackSpace',
          'delete': 'Delete', 'escape': 'Escape', 'esc': 'Escape', 'left': 'Left', 'right': 'Right', 'up': 'Up',
          'down': 'Down', 'home': 'Home', 'end': 'End', 'pageup': 'Prior', 'pagedown': 'Next',
          'cmd': 'super', 'command': 'super', 'option': 'alt', 'control': 'ctrl'}


def parse_combo(combo: str) -> tuple[list[str], str]:
    parts = [p.strip().lower() for p in str(combo).split('+') if p.strip()]
    if not parts or len(parts) > 5:
        raise ValueError(f'Invalid key combo: {combo!r}')
    return parts[:-1], parts[-1]


def validate_steps(steps) -> list[dict]:
    """steps: [{"key": "cmd+s"} | {"key": "right", "hold_ms": 800} | {"text": "hello"} | {"wait_ms": 200}]"""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f'Provide 1–{MAX_STEPS} input steps.')
    clean = []
    for step in steps:
        if not isinstance(step, dict) or len(set(step) & {'key', 'text', 'wait_ms'}) != 1:
            raise ValueError('Each step needs exactly one of key, text or wait_ms.')
        if 'key' in step:
            mods, key = parse_combo(step['key'])
            hold = int(step.get('hold_ms', 0) or 0)
            if not 0 <= hold <= MAX_HOLD_MS:
                raise ValueError(f'hold_ms must be 0–{MAX_HOLD_MS}.')
            clean.append({'key': key, 'mods': mods, 'hold_ms': hold})
        elif 'text' in step:
            text = step['text']
            if not isinstance(text, str) or not 1 <= len(text) <= 2000:
                raise ValueError('Text steps need 1–2,000 characters.')
            clean.append({'text': text})
        else:
            wait = int(step['wait_ms'])
            if not 0 <= wait <= MAX_HOLD_MS:
                raise ValueError(f'wait_ms must be 0–{MAX_HOLD_MS}.')
            clean.append({'wait_ms': wait})
    return clean


class _Mac:
    def __init__(self):
        cg = ctypes.util.find_library('ApplicationServices')
        cf = ctypes.util.find_library('CoreFoundation')
        if not cg or not cf:
            raise RuntimeError('CoreGraphics is unavailable.')
        self.cg = ctypes.cdll.LoadLibrary(cg)
        self.cf = ctypes.cdll.LoadLibrary(cf)
        self.cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        self.cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        self.cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self.cg.CGEventPostToPid.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self.cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)]
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]

    def _post(self, pid, code, down, flags=0, text=None):
        event = self.cg.CGEventCreateKeyboardEvent(None, code, down)
        if not event:
            raise RuntimeError('Could not create a keyboard event.')
        try:
            if flags:
                self.cg.CGEventSetFlags(event, flags)
            if text is not None:
                units = text.encode('utf-16-le')
                buf = (ctypes.c_uint16 * (len(units) // 2)).from_buffer_copy(units)
                self.cg.CGEventKeyboardSetUnicodeString(event, len(buf), buf)
            self.cg.CGEventPostToPid(pid, event)
        finally:
            self.cf.CFRelease(event)

    def key(self, pid, key, mods, hold_ms):
        if key not in MAC_KEYS:
            raise ValueError(f'Unsupported key on macOS: {key}')
        flags = 0
        for mod in mods:
            if mod not in MAC_FLAGS:
                raise ValueError(f'Unsupported modifier: {mod}')
            flags |= MAC_FLAGS[mod]
        self._post(pid, MAC_KEYS[key], True, flags)
        time.sleep(max(hold_ms, 30) / 1000)
        self._post(pid, MAC_KEYS[key], False, flags)

    def text(self, pid, text):
        for char in text:
            self._post(pid, 0, True, text=char)
            self._post(pid, 0, False, text=char)
            time.sleep(0.012)


class _Windows:
    WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102

    def __init__(self):
        self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    def _windows(self, pid):
        found = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def collect(hwnd, _):
            owner = ctypes.c_ulong()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and self.user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        self.user32.EnumWindows(proc(collect), 0)
        if not found:
            raise RuntimeError('The process has no visible window to receive input.')
        return found

    def key(self, pid, key, mods, hold_ms):
        if key not in WIN_KEYS or any(m not in WIN_KEYS for m in mods):
            raise ValueError(f'Unsupported key on Windows: {key}')
        for hwnd in self._windows(pid):
            for mod in mods:
                self.user32.PostMessageW(hwnd, self.WM_KEYDOWN, WIN_KEYS[mod], 0)
            self.user32.PostMessageW(hwnd, self.WM_KEYDOWN, WIN_KEYS[key], 0)
            time.sleep(max(hold_ms, 30) / 1000)
            self.user32.PostMessageW(hwnd, self.WM_KEYUP, WIN_KEYS[key], 0xC0000001)
            for mod in reversed(mods):
                self.user32.PostMessageW(hwnd, self.WM_KEYUP, WIN_KEYS[mod], 0xC0000001)

    def text(self, pid, text):
        for hwnd in self._windows(pid):
            for char in text:
                self.user32.PostMessageW(hwnd, self.WM_CHAR, ord(char), 0)


class _X11:
    def __init__(self):
        self.xdotool = shutil.which('xdotool')
        if not self.xdotool:
            raise RuntimeError('Install xdotool for background input on Linux (X11 only; Wayland blocks it).')

    def _window(self, pid):
        out = subprocess.run([self.xdotool, 'search', '--pid', str(pid)], capture_output=True, text=True, timeout=5)
        ids = out.stdout.split()
        if not ids:
            raise RuntimeError('The process has no X11 window.')
        return ids[0]

    def key(self, pid, key, mods, hold_ms):
        combo = '+'.join([X_KEYS.get(m, m) for m in mods] + [X_KEYS.get(key, key)])
        window = self._window(pid)
        if hold_ms:
            subprocess.run([self.xdotool, 'keydown', '--window', window, combo], timeout=5, check=True)
            time.sleep(hold_ms / 1000)
            subprocess.run([self.xdotool, 'keyup', '--window', window, combo], timeout=5, check=True)
        else:
            subprocess.run([self.xdotool, 'key', '--window', window, combo], timeout=5, check=True)

    def text(self, pid, text):
        subprocess.run([self.xdotool, 'type', '--window', self._window(pid), text], timeout=30, check=True)


def backend():
    system = platform.system()
    if system == 'Darwin':
        # Verified 2026-11-16 (macOS 27, Apple silicon): keyboard events posted to an
        # inactive app's pid are dropped by AppKit/SDL (no key window), so this would
        # report delivery without effect. Refuse and name what does work.
        raise RuntimeError('macOS drops keyboard events sent to apps that are not frontmost. Without focus, use '
                           'desktop_call semantic actions (click via accessibility press, type, set-value, select, '
                           'toggle, scroll) for normal apps, and game memory reads/writes for games. Ask the user to '
                           'say "show me" for steps that need real keystrokes.')
    if system == 'Windows':
        return _Windows()
    if system == 'Linux':
        return _X11()
    raise RuntimeError(f'Background input is not supported on {system}.')


def send(pid: int, steps, check=lambda: None, engine=None) -> dict:
    if type(pid) is not int or pid <= 1:
        raise ValueError('Provide the target process ID (from list-apps or game targets).')
    clean = validate_steps(steps)
    engine = engine or backend()
    done = 0
    for step in clean:
        check()
        if 'key' in step:
            engine.key(pid, step['key'], step['mods'], step['hold_ms'])
        elif 'text' in step:
            engine.text(pid, step['text'])
        else:
            time.sleep(step['wait_ms'] / 1000)
        done += 1
    return {'pid': pid, 'steps_sent': done, 'platform': platform.system(), 'focus_changed': False,
            'verification': 'Delivery is not proof of effect. Verify with a screenshot, accessibility read or memory read.'}
