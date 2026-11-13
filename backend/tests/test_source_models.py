"""Resource choices survive imports/restarts without changing global inference."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
import threading

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.local_media import LocalMediaRuntime
from bughunter.providers import DEFAULT_SETTINGS, RunManager
from bughunter.source_models import validate
from bughunter.store import Store

OPUS = {'provider':'claude_subscription', 'model':'claude-opus-4-7'}
CODEX = {'provider':'codex_subscription', 'model':'gpt-6.1-sol'}


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path/'data')
    runtime = LocalMediaRuntime(store)
    service = KnowledgeService(store, start_worker=False, media_runtime=runtime)
    yield service
    service.shutdown()


def upload(text):
    return {'filename':text+'.txt', 'content_base64':base64.b64encode(text.encode()).decode()}


@pytest.mark.parametrize('kind', ['files', 'paths', 'folder', 'zip', 'url'])
def test_batch_import_inherits_before_scheduling(service, tmp_path, monkeypatch, kind):
    folder = tmp_path/'lessons'; folder.mkdir()
    for name in ('first', 'second'):
        (folder/(name+'.txt')).write_text(name)
    scheduled = []
    monkeypatch.setattr(service, '_schedule', lambda ident:scheduled.append(service.store.get('source', ident)))
    if kind == 'zip':
        archive = tmp_path/'lessons.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            for path in folder.iterdir(): out.write(path, path.name)
        monkeypatch.setattr(service.courses, '_launch', lambda ident:None)
        batch = service.dispatch('courses.import', {'path':str(archive), 'analysis_model':OPUS})
        assert batch['analysis_model'] == OPUS
        service.courses.run_archive(batch['id'])
    elif kind == 'url':
        service.import_source(url='https://example.com/lesson', analysis_model=OPUS)
    else:
        value = [upload('first'), upload('second')] if kind == 'files' else [str(p) for p in folder.iterdir()] if kind == 'paths' else str(folder)
        service.dispatch('sources.import', {kind:value, 'analysis_model':OPUS})
    assert len(scheduled) == (1 if kind == 'url' else 2)
    assert all(s['analysis_model'] == OPUS for s in scheduled)


def test_invalid_model_never_schedules_and_batch_update_is_atomic(service, monkeypatch):
    one = service.import_source(**upload('one'))
    two = service.import_source(**upload('two'))
    scheduled = []
    monkeypatch.setattr(service, '_schedule', scheduled.append)
    with pytest.raises(ValueError):
        service.import_source(files=[upload('three')], analysis_model={**OPUS, 'model':'missing'})
    assert not scheduled and len(service.store.list('source')) == 2
    with pytest.raises(ValueError):
        service.set_models([one['id'], 'missing', two['id']], OPUS)
    assert service.store.get('source', one['id']).get('analysis_model') is None
    assert service.store.get('source', two['id']).get('analysis_model') is None
    assert not scheduled


def test_choice_persists_on_restart_and_null_resets_to_global(service):
    baseline = service.media_runtime.config()
    source = service.import_source(**upload('one'), analysis_model=OPUS)
    restarted = KnowledgeService(Store(service.store.root), start_worker=False, media_runtime=service.media_runtime)
    try:
        result = restarted.get_source(source['id'])
        assert result['analysis_model'] == result['effective_analysis_model'] == OPUS
        assert restarted._source_config(result)['claude_vision_consent']
        restarted.dispatch('sources.models', {'ids':[source['id']], 'analysis_model':None})
        reset = restarted.get_source(source['id'])
        assert reset['analysis_model'] is None
        assert reset['effective_analysis_model']['provider'] == baseline['vision_provider']
        assert service.media_runtime.config() == baseline
    finally: restarted.shutdown()


def test_model_update_never_requeues_completed_or_cancelled_sources(service, monkeypatch):
    sources = [service.store.put('source', {'status':status}) for status in ('ready','partial','cancelled','processing')]
    def forbidden(*args): raise AssertionError('Model selection unexpectedly scheduled extraction')
    monkeypatch.setattr(service, '_schedule', forbidden)
    service.set_models([s['id'] for s in sources], OPUS)
    assert [service.store.get('source', s['id'])['status'] for s in sources] == ['ready','partial','cancelled','processing']


def test_changing_provider_releases_only_the_old_providers_cooldown(service):
    source=service.store.put('source',{'status':'queued','analysis_model':CODEX,
        'provider_wait':{'provider':'codex_subscription','model':CODEX['model'],'retry_at':9999999999},
        'media_progress':{'phase':'provider_wait','resume_phase':'frames','analyzed':24}})
    service.set_models([source['id']],OPUS)
    updated=service.store.get('source',source['id'])
    assert updated['status']=='queued' and 'provider_wait' not in updated
    assert updated['last_provider_wait']['provider']=='codex_subscription'
    assert updated['media_progress']['analyzed']==24 and updated['media_progress']['phase']=='frames'


def test_profile_change_applies_next_frame_and_completed_evidence_is_reused(service, tmp_path, monkeypatch):
    runtime = service.media_runtime
    runtime.configure({'adaptive_frames':False, 'frame_interval':2, 'max_frames':0,
                       'claude_parallel_enabled':True, 'claude_vision_consent':True,
                       'codex_detail_model':'different-model'})
    baseline = runtime.config()
    source = service.store.put('source', {'title':'Course', 'status':'processing',
        'content_hash':'fixture', 'analysis_model':CODEX})
    output = service.store.root/'artifacts'/source['id']; output.mkdir()
    video = tmp_path/'video.mp4'; video.write_bytes(b'video')
    def run(args, timeout):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'6'}, 'streams':[{'codec_type':'video','duration':'6'}]}))
        Path(args[-1]).write_bytes(b'frame '+args[args.index('-ss')+1].encode())
        return SimpleNamespace(stdout='', stderr='')
    calls = []
    def vision(image, stamp, **kwargs):
        config = kwargs['config']
        assert not config['claude_parallel_enabled'] and not config['codex_detail_model']
        calls.append((stamp, config['vision_provider']))
        if stamp == 0: service.set_models([source['id']], OPUS)
        return 'Visible facts: Cobalt panel. Inferred intent: compare screens.'
    monkeypatch.setattr(service, '_run', run)
    monkeypatch.setattr(runtime, 'vision', vision)
    monkeypatch.setattr('bughunter.local_media.binary', lambda name:name)
    segments, _ = service._extract_media(video, output, source)
    assert calls == [(0., 'codex_subscription'), (2., 'claude_subscription'), (4., 'claude_subscription')]
    visuals = [s for s in segments if s['modality'] == 'vision']
    assert [s['model'] for s in visuals] == ['gpt-6.1-sol', 'claude-opus-4-7', 'claude-opus-4-7']
    assert service.store.get('source', source['id'])['sampling_plan']['planned'] == [0.,2.,4.]
    calls.clear()
    service.set_models([source['id']], CODEX)
    service._extract_media(video, output, service.store.get('source', source['id']))
    assert not calls
    assert runtime.config() == baseline


@pytest.mark.parametrize('profile,provider', [(OPUS,'claude_code'), (CODEX,'codex'),
    ({'provider':'local','model':'qwen3-vl:2b'},'ollama')])
def test_study_snapshot_uses_source_choice_without_global_changes(service, monkeypatch, profile, provider):
    settings = {**DEFAULT_SETTINGS, 'provider':'deepseek', 'model':'deepseek-chat',
                'local_only':True, 'custom_system_prompt':'keep', 'api_base_url':'https://api.deepseek.com'}
    app = SimpleNamespace(store=service.store, settings=SimpleNamespace(get=lambda:dict(settings)))
    runs = RunManager(app)
    source = service.store.put('source', {'status':'ready', 'chunk_count':1, 'analysis_model':profile})
    checked = []
    monkeypatch.setattr(runs, '_preflight', lambda config:checked.append(config))
    monkeypatch.setattr(runs, '_launch', lambda run:None)
    run = runs.start_study(source['id'])
    assert run['provider'] == provider and run['model'] == profile['model']
    assert checked[0]['custom_system_prompt'] == 'keep'
    assert checked[0]['local_only'] == (provider == 'ollama')
    assert app.settings.get() == settings
    if provider == 'ollama': assert checked[0]['api_base_url'] == 'http://127.0.0.1:11434'


def test_local_custom_models_still_require_capabilities(monkeypatch):
    monkeypatch.setattr('bughunter.media_models.discover', lambda:[{'id':'custom:v1','local':False,'roles':['vision']}])
    with pytest.raises(ValueError): validate({'provider':'local','model':'custom:v1'})
    monkeypatch.setattr('bughunter.media_models.discover', lambda:[{'id':'custom:v1','local':True,'roles':['vision','chat']}])
    assert validate({'provider':'local','model':'custom:v1'})['model'] == 'custom:v1'


@pytest.mark.parametrize('provider,count', [('local',1), ('claude_subscription',2), ('codex_subscription',2)])
def test_hosted_ingestion_has_two_workers(tmp_path, provider, count):
    store = Store(tmp_path)
    store.set_setting('local_media', {'vision_provider':provider})
    service = KnowledgeService(store, media_runtime=LocalMediaRuntime(store))
    try: assert len(service._workers) == count
    finally: service.shutdown()


def test_explicit_hosted_source_adds_second_lane_with_local_global(tmp_path):
    store = Store(tmp_path)
    service = KnowledgeService(store, media_runtime=LocalMediaRuntime(store))
    try:
        assert len(service._workers) == 1
        service.import_source(**upload('text'), analysis_model=OPUS)
        assert len(service._workers) == 2
        assert service.media_runtime.config()['vision_provider'] == 'local'
    finally: service.shutdown()


@pytest.mark.parametrize('status', [429, 503])
def test_source_study_cannot_enter_global_fallback_chain(service, monkeypatch, status):
    import httpx
    from bughunter.app import Application
    app = Application(service.store.root/'isolated', primary=False)
    profile = {'provider':'local','model':'qwen3-vl:2b'}
    source = app.store.put('source', {'status':'ready', 'chunk_count':1, 'analysis_model':profile})
    monkeypatch.setattr(app.runs, '_preflight', lambda config:None)
    monkeypatch.setattr(app.runs, '_launch', lambda run:None)
    run = app.runs.start_study(source['id'])
    def forbidden(*args): raise AssertionError('Explicit source selection entered fallback chain')
    monkeypatch.setattr('bughunter.capabilities.resolve_fallback_provider', forbidden)
    def post(client, url, **kwargs):
        if url.endswith('/api/show'): return httpx.Response(200, json={'capabilities':['tools']}, request=httpx.Request('POST',url))
        assert kwargs['json']['model'] == profile['model']
        return httpx.Response(status, json={'error':'fixture'}, request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    try:
        if status == 503:
            with pytest.raises(httpx.HTTPStatusError): app.runs._api(run, threading.Event())
        else:
            assert 'rate limit' in app.runs._api(run, threading.Event())
    finally: app.shutdown()


@pytest.fixture
def cached_video(service, tmp_path, monkeypatch):
    runtime = service.media_runtime
    runtime.configure({'adaptive_frames':False, 'frame_interval':2, 'max_frames':0,
                       'local_vision_low_memory':True})
    source = service.store.put('source', {'title':'Cached lesson', 'status':'processing',
        'content_hash':'original', 'analysis_model':{'provider':'local','model':'qwen3-vl:2b'}})
    artifacts = service.store.root/'artifacts'/source['id']; artifacts.mkdir()
    video = tmp_path/'cached.mp4'; video.write_bytes(b'video')
    calls = []
    narration = [{'modality':'transcript','text':'Original narration','start':0,'end':6,'locator':'Speech'}]
    def run(args, timeout):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'6'},'streams':[{'codec_type':'video','duration':'6'}]}))
        Path(args[-1]).write_bytes(b'frame '+args[args.index('-ss')+1].encode())
        return SimpleNamespace(stdout='', stderr='')
    def vision(image, stamp, **kwargs):
        calls.append((stamp, kwargs['config']['vision_provider']))
        return f'Visible facts: Cobalt fixture at {stamp}. Narrator: original demonstration.'
    monkeypatch.setattr(service, '_run', run)
    monkeypatch.setattr(runtime, 'vision', vision)
    monkeypatch.setattr('bughunter.local_media.binary', lambda name:name)
    def extract(worker=service, speech=narration):
        return worker._extract_media(video, artifacts, worker.store.get('source', source['id']), transcript=speech)
    return SimpleNamespace(service=service, runtime=runtime, source=source, artifacts=artifacts,
                           calls=calls, narration=narration, extract=extract)


def test_local_profile_restart_reuses_own_cached_descriptions(cached_video, monkeypatch):
    fixture = cached_video
    fixture.extract()
    assert len(fixture.calls) == 3
    cited = {row['id'] for row in fixture.service.search('Cobalt')}
    fixture.calls.clear()
    restarted = KnowledgeService(Store(fixture.service.store.root), start_worker=False, media_runtime=fixture.runtime)
    monkeypatch.setattr(restarted, '_run', fixture.service._run)
    try:
        fixture.extract(restarted)
        assert not fixture.calls
        assert {row['id'] for row in restarted.search('Cobalt')} == cited
    finally: restarted.shutdown()


@pytest.mark.parametrize('first,second', [('local','claude_subscription'), ('claude_subscription','local')])
def test_profile_switch_reuses_original_context_across_low_memory_modes(cached_video, first, second):
    fixture = cached_video
    profiles = {'local':{'provider':'local','model':'qwen3-vl:2b'}, 'claude_subscription':OPUS}
    fixture.service.set_models([fixture.source['id']], profiles[first])
    fixture.extract()
    cited = {row['id'] for row in fixture.service.search('Cobalt')}
    fixture.calls.clear()
    fixture.service.set_models([fixture.source['id']], profiles[second])
    fixture.extract()
    assert not fixture.calls
    assert {row['id'] for row in fixture.service.search('Cobalt')} == cited


@pytest.mark.parametrize('change', ['image','narration','source_hash','unsafe_previous','rejected'])
def test_preserved_cache_still_invalidates_changed_or_rejected_evidence(cached_video, change):
    fixture = cached_video
    fixture.service.set_models([fixture.source['id']], OPUS)
    fixture.extract()
    timeline = fixture.artifacts/'media_timeline.json'
    original = timeline.read_text()
    if change == 'image':
        (fixture.artifacts/'frame-000000000000.jpg').write_bytes(b'changed image')
    elif change == 'source_hash':
        fixture.service._update(fixture.source['id'], content_hash='new hash')
    elif change == 'unsafe_previous':
        rows = json.loads(original)
        visual = next(row for row in rows if row.get('modality') == 'vision' and row['start'] == 2)
        visual['previous_frames'][0]['artifact_id'] = '../outside.jpg'
        timeline.write_text(json.dumps(rows))
    elif change == 'rejected':
        chunk = next(row for row in fixture.service.search('Cobalt') if row['modality'] == 'vision')
        fixture.service.reject(chunk['id'], 'Incorrect visible evidence')
        # A stale cached file must not undo the operator's withdrawal.
        timeline.write_text(original)
    fixture.calls.clear()
    fixture.service.set_models([fixture.source['id']], {'provider':'local','model':'qwen3-vl:2b'})
    speech = [{**fixture.narration[0], 'text':'Changed narration'}] if change == 'narration' else fixture.narration
    fixture.extract(speech=speech)
    assert fixture.calls
    if change == 'rejected':
        assert chunk['id'] not in {row['id'] for row in fixture.service.search('Cobalt')}
