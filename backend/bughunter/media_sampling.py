"""Adaptive sampling of local tutorial frames. No external network or uploaded frames."""
from __future__ import annotations

import re
import subprocess

_PTS = re.compile(r'pts_time:\s*(-?\d+(?:\.\d+)?)')


def scene_cuts(ffmpeg: str, path: str, duration: float, threshold: float = 0.30,
               max_events: int = 1200, run=None) -> list[float]:
    """Find low-resolution scene changes across the recording, with a hard timeout.

    FFmpeg exits nonzero for files that cannot be decoded; that is a soft failure
    for course ingestion, which still retains fixed-interval frames.
    """
    if duration <= 0 or not 0.05 <= threshold <= 0.95: return []
    # Decoding for a scene pass is CPU-intensive; analyze one scaled frame every
    # 2s and cap runtime. The detector never invents timestamps when it times out.
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'info',
        '-i', path, '-an', '-vf', f'fps=1/2,scale=320:-2,select=gt(scene\\,{threshold}),showinfo',
        '-f', 'null', '-']
    try:
        timeout = min(1200, max(75, duration/3))
        done = (run(command, timeout) if run else
                subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=timeout, check=False))
        if done.returncode: return []
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
