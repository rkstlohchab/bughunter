import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.local_media import LocalMediaRuntime
from bughunter.media_context import context_key, narration_near
from bughunter.store import Store


@pytest.fixture
def service(tmp_path):
    k = KnowledgeService(Store(tmp_path / 'data'), start_worker=False)
    yield k
    k.shutdown()


def test_narration_window_is_bounded_and_cache_changes_with_evidence(tmp_path):
    speech = [dict(text='earlier', start=0, end=10, modality='transcript'),
              dict(text='Open the request editor.', start=50, end=70, modality='transcript', locator='00:00:50'),
              dict(text='unrelated image', start=60, end=61, modality='vision'),
              dict(text='future', start=300, end=320, modality='transcript')]
    context = narration_near(speech, 60, max_chars=12)
    assert context == [dict(text='Open the req', start=50, end=70, locator='00:00:50')]
    frame = tmp_path / 'frame.jpg'; frame.write_bytes(b'frame one')
    evidence = dict(path=str(frame), time=60)
    first = context_key('source', 'model', evidence, [], context)
    assert first != context_key('source', 'model', evidence, [], [])
    frame.write_bytes(b'frame two')
    assert first != context_key('source', 'model', evidence, [], context)


def test_progressive_publication_preserves_corrections_assertions_and_manual_frames(service):
    s = service.store.put('source', dict(title='Tutorial', status='processing'))
    out = service.store.root / 'artifacts' / s['id']; out.mkdir()
    speech = [dict(text='Compare the two account requests.', locator='00:01:00', start=60, end=70, modality='transcript')]
    service._publish_media(s, speech, out)
    chunk = service.get_source(s['id'])['chunks'][0]
    service.correct(chunk['id'], 'Compare two test-account requests.')
    assertion = service.enrich(s['id'], [dict(chunk_id=chunk['id'], quote='two test-account requests', text='Compare account access.')])
    frame = out / 'manual.jpg'; frame.write_bytes(b'frame')
    service._update(s['id'], frames=[dict(path=str(frame), time=65, locator='Frame at 65')])
    manual = service.interpret_frame(s['id'], frame.name, 'The response panel is visible.')
    visuals = [dict(text='Visible facts: response status 403. Narrator explains ownership.', locator='Frame at 00:01:10', start=70, modality='vision')]
    count = service._publish_media(s, speech + visuals, out)
    assert service._publish_media(s, speech + visuals, out) == count == 3
    assert service._index(s, speech + visuals, preserve_existing=True) == 3
    current = service.get_source(s['id'])
    assert current['status'] == 'processing'
    assert next(c for c in current['chunks'] if c['id'] == chunk['id'])['text'] == 'Compare two test-account requests.'
    assert manual['id'] in {c['id'] for c in current['chunks']}
    nodes = {n['id']: n for n in service.graph()['nodes']}
    assert assertion['assertion_ids'][0] in nodes
    assert nodes[manual['id']]['status'] == 'inferred'
    assert service.search('ownership')
    assert service.integrity()['status'] == 'healthy'


def test_speech_is_searchable_before_visual_scan_and_frame_context_is_published(service, tmp_path, monkeypatch):
    s = service.store.put('source', dict(title='Tutorial', status='processing', content_hash='fixture'))
    out = service.store.root / 'artifacts' / s['id']; out.mkdir()
    media = tmp_path / 'lesson.mp4'; media.write_bytes(b'media')
    runtime = LocalMediaRuntime(service.store)
    runtime.configure(dict(frame_interval=2, max_frames=3))
    service.media_runtime = runtime
    speech = [dict(text='Open the response editor.', locator='00:00:00', start=0, end=6, modality='transcript')]
    def run(args, timeout=1800):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps(dict(format=dict(duration='6'), streams=[dict(codec_type='video', duration='6')])))
        Path(args[-1]).write_bytes(b'frame at ' + args[args.index('-ss')+1].encode())
        return SimpleNamespace(stdout='')
    def scenes(*args, **kwargs):
        assert service.search('response editor')
        assert service.store.get('source', s['id'])['status'] == 'processing'
        return []
    calls = []
    def vision(path, timestamp, **kwargs):
        calls.append((timestamp, kwargs))
        assert kwargs['context'][0]['text'] == speech[0]['text']
        kwargs['cancel_check']()
        if timestamp > 0:
            assert service.search('visible account panel')  # first frame published already
        return f'Visible account panel at {timestamp}; reusable lesson: compare responses.'
    monkeypatch.setattr(service, '_run', run)
    monkeypatch.setattr('bughunter.local_media.binary', lambda name: name)
    monkeypatch.setattr('bughunter.media_sampling.scene_cuts', scenes)
    monkeypatch.setattr(runtime, 'vision', vision)
    segments, _ = service._extract_media(media, out, s, transcript=speech)
    assert [len(c[1]['previous_frames']) for c in calls] == [0, 1, 2]
    visual = [x for x in segments if x['modality'] == 'vision']
    assert len(visual) == 3 and all(x['context_version'] == 1 for x in visual)
    assert all(x['assertion_status'] == 'inferred' for x in visual)
    assert service.store.get('source', s['id'])['visual_context_count'] == 3
    assert len(service.get_source(s['id'])['chunks']) == 4
    calls.clear()
    service._extract_media(media, out, s, transcript=speech)
    assert calls == []  # matching evidence reuses the completed visual cache
    assert service.integrity()['status'] == 'healthy'


def test_cancel_or_delete_prevents_late_index_publication(service, tmp_path):
    s = service.store.put('source', dict(title='Tutorial', status='processing'))
    out = tmp_path / 'artifacts'; out.mkdir()
    segments = [dict(text='Later evidence', locator='00:00:05', start=5)]
    service._update(s['id'], status='cancelled')
    with pytest.raises(InterruptedError):
        service._publish_media(s, segments, out)
    service.store.delete('source', s['id'])
    assert service._index(s, segments, preserve_existing=True) == 0
    assert service.store.get('source', s['id']) is None


def test_audio_publication_keeps_unvisited_visual_checkpoints(service, tmp_path):
    s = service.store.put('source', dict(title='Tutorial', status='processing'))
    speech = [dict(text='Narration', locator='00:00:00', start=0, modality='transcript')]
    cached = [dict(text='Later completed screen analysis', locator='Frame at 00:10:00', start=600, modality='vision')]
    service._publish_media(s, speech, tmp_path, cached=cached)
    assert cached[0] in json.loads((tmp_path/'media_timeline.json').read_text())
    assert service.search('Narration')
    assert not service.search('Later completed screen analysis')  # no unvalidated cache republished
