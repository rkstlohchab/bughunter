"""Adaptive sampling of local tutorial frames. No external network or uploaded frames."""
from __future__ import annotations

import re
import subprocess

_PTS = re.compile(r'pts_time:\s*(-?\d+(?:\.\d+)?)')


def scene_cuts(ffmpeg: str, path: str, duration: float, threshold: float = 0.30,
               max_events: int = 1200, run=None, *, start=0, strict=False) -> list[float]:
    """Find low-resolution scene changes across the recording, with a hard timeout.

    FFmpeg exits nonzero for files that cannot be decoded; that is a soft failure
    for course ingestion, which still retains fixed-interval frames.
    """
    if duration <= 0 or not 0.05 <= threshold <= 0.95: return []
    # Decoding for a scene pass is CPU-intensive; analyze one scaled frame every
    # 2s and cap runtime. The detector never invents timestamps when it times out.
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'info',
        '-ss', str(start), '-i', path, '-t', str(duration), '-an', '-vf', f'fps=1/2,scale=320:-2,select=gt(scene\\,{threshold}),showinfo',
        '-f', 'null', '-']
    try:
        timeout = min(1200, max(75, duration/3))
        done = (run(command, timeout) if run else
                subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=timeout, check=False))
        if done.returncode:
            if strict: raise ValueError(f'FFmpeg scene scan exited {done.returncode}')
            return []
        times = []
        stderr = done.stderr if isinstance(done.stderr, str) else done.stderr.decode('utf-8','replace')
        for match in _PTS.finditer(stderr):
            value=float(match.group(1))
            if 0 < value < duration and (not times or value-times[-1] >= 1.5):
                times.append(round(value,2))
                if len(times) >= max_events: break
        return times
    except InterruptedError:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError):
        if strict: raise
        return []


def plan_frames(duration: float, interval: float, max_frames: int = 0,
                scene_times: list[float] | None = None) -> list[float]:
    if not 2 <= interval <= 120 or duration < 0:
        raise ValueError('Invalid video sampling parameters')
    regular_count = max(1, int(duration/interval) + (1 if duration%interval else 0))
    cap = max(0,int(max_frames))
    total = min(regular_count,cap) if cap else regular_count
    step=max(interval,duration/total) if cap and duration else interval
    regular=[min(max(0,duration-.1),round(n*step,2)) if duration else 0.0 for n in range(total)]
    # Uncapped videos may be long, but adaptive *additional* frames are bounded.
    scene = []
    for stamp in sorted(set(float(t) for t in (scene_times or []))):
        if 0 <= stamp < duration and len(scene)<1200 and all(abs(stamp-x)>=1.5 for x in regular):
            if not scene or stamp-scene[-1]>=1.5:
                scene.append(round(stamp,2))
    planned=sorted(set(regular+scene))
    # Max frames is an absolute ceiling when explicitly configured.
    if cap and len(planned)>cap:
        selected=list(regular)
        for t in scene:
            if len(selected)>=cap: break
            selected.append(t)
        planned=sorted(set(selected))
    return planned


def scan_windows(ffmpeg, path, duration, checkpoint, identity, *, threshold=.30,
                 run, progress=None, check=None):
    """Commit only successfully decoded ten-minute windows; retry recorded gaps."""
    import json
    import math
    from .media_context import save_json
    identity = {**identity, 'version': 2, 'window_seconds': 600, 'threshold': threshold}
    try:
        saved = json.loads(checkpoint.read_text())
    except (OSError, ValueError):
        saved = {}
    windows = saved.get('windows', {}) if saved.get('identity') == identity else {}
    total = math.ceil(duration / 600)
    gaps = []
    for index in range(total):
        if check: check()
        start, length = index * 600, min(600, duration-index*600)
        slot = str(index)
        if windows.get(slot, {}).get('status') != 'complete':
            try:
                relative = scene_cuts(ffmpeg, path, length, threshold=threshold, run=run, start=start, strict=True)
                times = [round(start+t,2) for t in relative]
                windows[slot] = {'status':'complete', 'start':start, 'end':start+length, 'times':times}
            except InterruptedError:
                raise
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                windows[slot] = {'status':'error', 'start':start, 'end':start+length, 'error':str(exc)[:300]}
            save_json(checkpoint, {'identity':identity, 'windows':windows})
        if windows[slot]['status'] != 'complete': gaps.append(windows[slot])
        if progress:
            progress({'phase':'scene_scan', 'completed':sum(w.get('status')=='complete' for w in windows.values()),
                      'analyzed':index+1, 'total':total, 'timestamp':start+length, 'duration':duration, 'gaps':gaps[:]})
    times = sorted(set(t for w in windows.values() if w.get('status')=='complete' for t in w['times']))
    return times[:1200], gaps
