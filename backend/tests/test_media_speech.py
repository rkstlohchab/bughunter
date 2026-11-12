"""Hermetic long-audio completion, recovery and provenance regressions."""
import sys
import subprocess
import types
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.media_speech import transcribe_chunks
from bughunter.store import Store

SRT = '1\n00:00:01,250 --> 00:00:02,500\nEvidence\n\n'


@pytest.fixture
def speech(tmp_path, monkeypatch):
    service = KnowledgeService(Store(tmp_path / 'db'), start_worker=False)
    source = service.store.put('source', {'id': 'speech', 'status': 'processing', 'content_hash': 'course'})
    path, model, artifacts = tmp_path / 'course.mp4', tmp_path / 'model.bin', tmp_path / 'artifacts'
    path.write_bytes(b'course'); model.write_bytes(b'model')
    calls = []
    def run(args, timeout):
        calls.append(args)
        if args[0] == 'ffmpeg':
            assert float(args[args.index('-t') + 1]) <= 600
            with wave.open(args[-1], 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * (16000 * int(float(args[args.index('-t') + 1]))))
        else:
            Path(args[args.index('-of') + 1] + '.srt').write_text(SRT)
    monkeypatch.setattr(service, '_run', run)
    def invoke(duration=1205):
        return transcribe_chunks(service, None, source, path, artifacts, duration, 'ffmpeg', 'whisper', model)
    return service, source, model, artifacts, calls, invoke


def test_full_duration_offsets_atomic_resume_and_model_identity(speech):
    service, source, model, artifacts, calls, invoke = speech
    # Legacy intermediates cannot prove a successful full-file transcription.
    artifacts.mkdir(); (artifacts / 'transcript.srt').write_text(SRT)
    (artifacts / 'speech_segments.json').write_text('[]')
    segments = invoke()
    assert [s['start'] for s in segments] == [1.25, 601.25, 1201.25]
    assert [s['end'] for s in segments] == [2.5, 602.5, 1202.5]
    assert segments[-1]['locator'] == '00:20:01–00:20:02'
    assert len(calls) == 6
    assert len(list(artifacts.rglob('chunk-*.srt'))) == 3
    assert not list(artifacts.rglob('*.wav'))
    assert not list(artifacts.rglob('*.partial.srt'))
    assert service.store.get('source', source['id'])['media_progress']['completed'] == 3
    calls.clear(); assert invoke() == segments; assert not calls
    model.write_bytes(b'updated model'); invoke(); assert len(calls) == 6


def test_interrupt_discards_partial_and_resumes_completed_chunks(speech, monkeypatch):
    service, _, _, artifacts, calls, invoke = speech
    normal = service._run
    def interrupted(args, timeout):
        normal(args, timeout)
        if args[0] == 'whisper' and '000001' in args[args.index('-of') + 1]:
            raise InterruptedError('stop')
    monkeypatch.setattr(service, '_run', interrupted)
    with pytest.raises(InterruptedError): invoke()
    assert len(list(artifacts.rglob('chunk-*.srt'))) == 1
    assert not list(artifacts.rglob('*.wav'))
    assert not list(artifacts.rglob('*.partial.srt'))
    calls.clear(); monkeypatch.setattr(service, '_run', normal)
    assert len(invoke()) == 3
    assert len(calls) == 4
    assert calls[0][calls[0].index('-ss') + 1] == '600'


def test_bad_transcript_never_becomes_complete(speech, monkeypatch):
    service, _, _, artifacts, _, invoke = speech
    normal = service._run
    def invalid(args, timeout):
        normal(args, timeout)
        if args[0] == 'whisper':
            Path(args[args.index('-of') + 1] + '.srt').write_text('partial garbage')
    monkeypatch.setattr(service, '_run', invalid)
    with pytest.raises(ValueError, match='malformed'): invoke()
    assert not list(artifacts.rglob('*.srt'))
    assert not list(artifacts.rglob('*.wav'))


def test_short_decoded_audio_is_not_committed(speech, monkeypatch):
    service, _, _, artifacts, calls, invoke = speech
    normal = service._run
    def truncated(args, timeout):
        normal(args, timeout)
        if args[0] == 'ffmpeg':
            with wave.open(args[-1], 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * 16000)
    monkeypatch.setattr(service, '_run', truncated)
    with pytest.raises(ValueError, match='cover the requested duration'): invoke()
    assert not any(args[0] == 'whisper' for args in calls)
    assert not list(artifacts.rglob('*.srt'))


def test_whisper_padded_tail_is_clipped_to_real_audio(speech, monkeypatch):
    service, _, _, _, _, invoke = speech
    normal = service._run
    def padded(args, timeout):
        normal(args, timeout)
        if args[0] == 'whisper':
            Path(args[args.index('-of') + 1] + '.srt').write_text(
                '1\n00:00:01,000 --> 00:00:30,000\nTail\n\n'
                '2\n00:00:20,000 --> 00:00:30,000\nOutside\n\n')
    monkeypatch.setattr(service, '_run', padded)
    assert [(s['start'], s['end'], s['text']) for s in invoke(3)] == [(1, 3, 'Tail')]


def test_scene_scan_uses_cancellable_runner():
    from bughunter.media_sampling import scene_cuts
    runner = Mock(return_value=Mock(returncode=0, stderr='pts_time:12.5'))
    assert scene_cuts('ffmpeg', 'course.mp4', 30000, run=runner) == [12.5]
    assert runner.call_args.args[1] == 1200
    runner.side_effect = InterruptedError('operator stop')
    with pytest.raises(InterruptedError):
        scene_cuts('ffmpeg', 'course.mp4', 30000, run=runner)


def test_per_source_cancel_interrupts_subprocess(speech):
    service, source, _, _, _, _ = speech
    service._processing.source_id = source['id']
    service._update(source['id'], media_control='cancel')
    with pytest.raises(InterruptedError, match='cancelled'):
        KnowledgeService._run(service, [sys.executable, '-c', 'raise AssertionError()'])


def test_cancel_during_subprocess_kills_child_and_retry_clears_control(speech, monkeypatch):
    service, source, _, _, _, _ = speech
    service._processing.source_id = source['id']
    child = Mock()
    def communicate(timeout=None):
        if timeout is not None:
            service._update(source['id'], media_control='cancel')
            raise subprocess.TimeoutExpired('whisper', timeout)
        return '', ''
    child.communicate.side_effect = communicate
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: child)
    with pytest.raises(InterruptedError, match='cancelled'):
        KnowledgeService._run(service, ['whisper'])
    child.kill.assert_called_once()
    service._update(source['id'], status='error')
    assert service.retry_source(source['id'])['media_control'] == 'resume'


@pytest.mark.parametrize('ocr_result', ['', 'Recovered heading'])
def test_pdf_short_original_survives_empty_ocr(tmp_path, monkeypatch, ocr_result):
    service = KnowledgeService(Store(tmp_path), start_worker=False)
    monkeypatch.setitem(sys.modules, 'pypdf', types.SimpleNamespace(PdfReader=lambda path:
                        types.SimpleNamespace(is_encrypted=False, pages=[Mock(extract_text=lambda: 'Heading')])))
    bitmap = types.SimpleNamespace(width=1, height=1, samples=b'\0\0\0')
    pdf = Mock(); pdf.__enter__ = Mock(return_value=[Mock(get_pixmap=lambda **kw: bitmap)])
    pdf.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, 'pymupdf', types.SimpleNamespace(open=lambda path: pdf, Matrix=lambda *a: None))
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(image_to_string=lambda image: ocr_result))
    segments, warnings = service._extract_pdf(tmp_path / 'source.pdf')
    assert not warnings
    assert segments[0]['text'] == (ocr_result or 'Heading')
    assert segments[0]['modality'] == ('ocr' if ocr_result else 'text')
