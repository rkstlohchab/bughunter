"""Identity and module observations shared by standalone verification commands."""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path
from ..memory_engine.protocol import read_process_start_time_ns


def process_image(pid: int) -> tuple[Path, str, int]:
    birth = read_process_start_time_ns(pid)
    if birth <= 0:
        raise ValueError('target process birth unavailable')
    if sys.platform == 'darwin':
        lib = ctypes.CDLL('/usr/lib/libproc.dylib')
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lib.proc_pidpath.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(pid, buf, len(buf)) <= 0:
            raise ValueError('target executable path unavailable')
        path = Path(os.fsdecode(buf.value)).resolve()
    elif sys.platform.startswith('linux'):
        path = Path(f'/proc/{pid}/exe').resolve(strict=True)
    else:
        raise ValueError('live executable discovery is not implemented for this platform')
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if read_process_start_time_ns(pid) != birth:
        raise ValueError('process changed while measuring executable')
    return path, digest, birth


def module_lookup(adapter):
    index = {}
    ambiguous = set()
    for module in adapter.modules():
        path = str(module.get('path') or '')
        raw = module.get('base', 0)
        try:
            base = int(raw, 0) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError):
            continue
        if not path or not 0 < base < 2**64 - 1:
            continue
        for key in (path, Path(path).name):
            if key in index and index[key] != base:
                ambiguous.add(key)
            index[key] = base
    def lookup(name):
        key = name if name in index else Path(name).name
        if key not in index or key in ambiguous:
            raise KeyError(f'module {name!r} is missing or ambiguous')
        return index[key]
    return lookup


def verify_running_bundle(executable, qualified_root, inputs):
    """Match a relocated bundle by every qualified input, never by its name."""
    executable=Path(executable).resolve(strict=True)
    qualified_root=Path(qualified_root).resolve(strict=True)
    main=next(Path(row['path']).resolve(strict=True) for row in inputs if row['role']=='main')
    relative_main=main.relative_to(qualified_root)
    root=executable
    for _ in relative_main.parts:
        root=root.parent
    if root.joinpath(relative_main).resolve()!=executable:
        raise ValueError('running executable is not at the qualified bundle-relative location')
    measured=[]
    for expected in inputs:
        relative=Path(expected['path']).resolve(strict=True).relative_to(qualified_root)
        path=root/relative
        resolved=path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError('running input escapes the matched bundle')
        with resolved.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=expected['sha256']:
            raise ValueError('running bundle input differs from qualified build: '+expected['role'])
        measured.append({'role':expected['role'],'path':str(resolved),'sha256':digest})
    return measured
