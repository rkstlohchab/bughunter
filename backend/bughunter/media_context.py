"""Bounded, timestamped evidence used when interpreting tutorial screenshots."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

CONTEXT_VERSION = 1


def narration_near(segments, timestamp, radius=30, max_chars=6000):
    result, remaining = [], max_chars
    for segment in segments:
        if segment.get('modality') != 'transcript':
            continue
        start, end = segment.get('start'), segment.get('end', segment.get('start'))
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end < timestamp-radius or start > timestamp+radius:
            continue
        text = str(segment.get('text') or '')[:remaining]
        if text:
            result.append({'text': text, 'start': start, 'end': end,
                           'locator': segment.get('locator', '')})
            remaining -= len(text)
        if remaining <= 0:
            break
    return result


def context_key(source_hash, model, frame, previous_frames, narration):
    images = [{'time': item['time'], 'sha256': hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()}
              for item in [*previous_frames, frame]]
    evidence = {'version': CONTEXT_VERSION, 'source_hash': source_hash, 'model': model,
                'images': images, 'narration': narration}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def retained_timeline(segments, cached):
    """Keep unvisited completed visual checkpoints when resuming an import."""
    entries = {(item.get('modality'), item.get('start'), item.get('locator')): item
               for item in [*cached, *segments]}
    return sorted(entries.values(), key=lambda s:(float(s.get('start',0)), s.get('modality','')))
