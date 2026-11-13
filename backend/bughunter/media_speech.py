"""Bounded, resumable local speech extraction with atomic completion markers."""
from __future__ import annotations

import hashlib
import json
import math
import wave
from pathlib import Path

CHUNK_SECONDS = 600


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def transcribe_chunks(service, runtime, source, path, artifacts, duration, ffmpeg, whisper, model, publish=None):
    """Only a successfully returned Whisper process can promote a chunk SRT.

    Old full-file WAV/SRT/JSON artifacts have no completion marker and are never
    reused here. Chunk identities include the source and exact model bytes.
    """
    from .knowledge import _seconds_label

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Cannot transcribe audio without a positive finite duration.')
    stat = path.stat()
    identity = {'version': 2, 'source_hash': source.get('content_hash') or _file_digest(path),
                'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
                'model_hash': _file_digest(model), 'duration': duration, 'chunk_seconds': CHUNK_SECONDS}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = artifacts / 'speech-v2' / key
    root.mkdir(parents=True, exist_ok=True)
    total = math.ceil(duration / CHUNK_SECONDS)
    segments = []
    def parse(text, index, length):
        local = service._subtitle_segments(text)
        if text.strip() and not local:
            raise ValueError(f'Transcript chunk {index + 1} is malformed.')
        for segment in local:
            if not 0 <= segment['start'] <= segment['end']:
                raise ValueError(f'Transcript chunk {index + 1} has invalid timestamps.')
        return local

    for index in range(total):
        if runtime:
            runtime.checkpoint(source['id'], service)
        service._check_cancelled(source['id'])
        start = index * CHUNK_SECONDS
        length = min(CHUNK_SECONDS, duration - start)
        service._update(source['id'], media_progress={'phase': 'speech', 'completed': index, 'analyzed': index,
                        'total': total, 'timestamp': start, 'duration': duration})
        complete = root / f'chunk-{index:06d}.srt'
        audio = root / f'chunk-{index:06d}.wav'
        partial = root / f'chunk-{index:06d}.partial.srt'
        try:
            if not complete.exists():
                audio.unlink(missing_ok=True)
                partial.unlink(missing_ok=True)
                service._run([ffmpeg, '-nostdin', '-y', '-ss', str(start), '-i', str(path),
                              '-t', str(length), '-map', '0:a:0', '-vn', '-ac', '1',
                              '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)], 1800)
                if runtime:
                    runtime.checkpoint(source['id'], service)
                with wave.open(str(audio), 'rb') as wav:
                    if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getnframes() == 0:
                        raise ValueError(f'Audio chunk {index + 1} is empty or malformed.')
                    if abs(wav.getnframes() / wav.getframerate() - length) > 1:
                        raise ValueError(f'Audio chunk {index + 1} does not cover the requested duration.')
                from .inference_gate import local_inference
                # Hosted imports may run concurrently; Whisper shares the same
                # local model lease as chat/vision on memory-limited machines.
                with local_inference(lambda:service._check_cancelled(source['id'])):
                    service._run([whisper, '-m', str(model), '-f', str(audio), '-osrt',
                                  '-of', str(partial.with_suffix('')), '-l', 'auto', '-nt'], 7200)
                service._check_cancelled(source['id'])
                if not partial.exists():
                    raise ValueError(f'Whisper did not write transcript chunk {index + 1}.')
                text = partial.read_text()
                parse(text, index, length)
                partial.replace(complete)
            text = complete.read_text()
            local = parse(text, index, length)
            for segment in local:
                # Whisper may timestamp its padded final inference window past EOF.
                if segment['start'] >= length:
                    continue
                begin = start + min(segment['start'], length)
                end = start + min(segment['end'], length)
                segments.append({**segment, 'start': begin, 'end': end,
                                 'locator': f'{_seconds_label(begin)}–{_seconds_label(end)}'})
        finally:
            audio.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
        if publish:
            publish(list(segments))
        service._update(source['id'], speech_coverage={'completed': index + 1, 'total': total, 'seconds': start + length, 'duration': duration}, media_progress={'phase': 'speech', 'completed': index + 1, 'analyzed': index + 1,
                        'total': total, 'timestamp': start + length, 'duration': duration})
    return segments
