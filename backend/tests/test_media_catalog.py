import json
import threading
import httpx
import pytest
from bughunter.local_media import LocalMediaRuntime
from bughunter.store import Store
from bughunter import media_models

@pytest.fixture
def runtime(tmp_path,monkeypatch):
    monkeypatch.setattr(media_models,'discover',lambda: [{'id':'qwen3-vl:2b','local':True,'roles':['chat','vision'],'digest':'sha:one'}])
    monkeypatch.setattr(LocalMediaRuntime,'_ollama_tags',lambda self:['qwen3-vl:2b'])
    runtime=LocalMediaRuntime(Store(tmp_path))
    monkeypatch.setattr(runtime,"start",lambda **kw:None)
    return runtime

def test_curated_sizes_and_roles(tmp_path,monkeypatch):
    monkeypatch.setattr(media_models,'discover',lambda:[])
    catalog=media_models.catalog(tmp_path)
    models=[m for m in catalog['vision_models'] if m['id'].startswith('huihui_ai/')]
    assert [m['download_bytes'] for m in models]==[1900000000,3300000000,6100000000]
    assert all(m['roles']==['chat','vision'] for m in models)
    assert catalog['disk']['reserve_bytes']==5*1024**3

def test_tags_never_imply_ready_and_digest_invalidation(runtime,monkeypatch):
    assert not runtime.status()['vision_ready']
    runtime.store.set_setting('media_verification',{'provider':'local','model':'qwen3-vl:2b','digest':'sha:one','ok':True})
    assert runtime.status()['vision_ready']
    monkeypatch.setattr(media_models,'discover',lambda:[])
    assert not runtime.status()['vision_ready']

def test_builtin_test_persists_evidence(runtime,monkeypatch):
    monkeypatch.setattr(runtime,'vision',lambda *a,**k:'Visible facts: A red square on the left and a blue circle on the right.\nUncertain or missing evidence: No narration is provided.')
    assert runtime.test({})['state']=='started'
    runtime._test_thread.join(3)
    assert runtime._test_job['state']=='ready'
    assert runtime.status()['vision_ready']
    assert len(runtime._test_job['image_sha256'])==64

def test_builtin_diagnostic_image_matches_the_shape_ground_truth(runtime,monkeypatch):
    from PIL import Image
    observed=[]
    def inspect(image,timestamp,**kwargs):
        with Image.open(image) as picture:
            red=[(x,y) for y in range(picture.height) for x in range(picture.width)
                 if picture.getpixel((x,y))==(255,0,0)]
        width=max(x for x,y in red)-min(x for x,y in red)
        height=max(y for x,y in red)-min(y for x,y in red)
        assert width==height
        assert kwargs['diagnostic'] is True
        observed.append(image)
        return 'Visible facts: Red square on the left and blue circle on the right.\nUncertain or missing evidence: None.'
    monkeypatch.setattr(runtime,'vision',inspect)
    runtime.test({});runtime._test_thread.join(3)
    assert observed and runtime._test_job['state']=='ready'

def test_failed_test_and_registered_path_validation(runtime,monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,'vision',lambda *a,**k:'Visible facts: generic text only.\nUncertain or missing evidence: Missing image.')
    runtime.test({}); runtime._test_thread.join(3)
    assert runtime._test_job['state']=='error'
    assert runtime._test_job['output'].startswith('Visible facts: generic text only.')
    assert not runtime.status()['vision_ready']
    runtime.store.put('source',{'id':'source_test','frames':[{'path':str(tmp_path/'outside.jpg'),'artifact_id':'outside.jpg'}]})
    with pytest.raises(ValueError): runtime.test({'source_id':'source_test','artifact_id':'outside.jpg'})
    with pytest.raises(ValueError): runtime.test({'frame_path':'/etc/passwd'})

def test_cancel_test(runtime,monkeypatch):
    def wait(*a,cancel_check,**kw):
        while True: cancel_check()
    monkeypatch.setattr(runtime,'vision',wait)
    runtime.test({}); runtime.cancel_setup(); runtime._test_thread.join(3)
    assert runtime._test_job['state']=='cancelled'

def test_setup_rejects_arbitrary_download(runtime):
    with pytest.raises(ValueError): runtime.setup({'target':'vision','role':'vision','model_id':'arbitrary/model:latest','approved':True})
    with pytest.raises(ValueError): runtime.setup({'target':'chat','role':'vision','model_id':media_models.CURATED[0]['id'],'approved':True})

def test_cloud_models_rejected():
    assert not media_models.local_capabilities({'capabilities':['vision'],'remote_host':'x'},'model')
    assert not media_models.local_capabilities({'capabilities':['vision']},'model:cloud')

def test_pull_stream_reports_progress_and_error(runtime,monkeypatch):
    original=httpx.AsyncClient
    lines=[{'status':'pulling','digest':'layer','completed':50,'total':100},
           {'status':'pulling','digest':'layer','completed':100,'total':100}, {'status':'success'}]
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text='\n'.join(json.dumps(x) for x in lines)))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    monkeypatch.setattr('bughunter.local_media.shutil.disk_usage',lambda path:type('Disk',(),{'free':20*1024**3})())
    runtime._pull('qwen3-vl:2b')
    assert runtime._job['completed_bytes']==100
    assert runtime._job['percent']==100
    lines[:]=[{'error':'no space left'}]
    with pytest.raises(ValueError,match='no space left'): runtime._pull('qwen3-vl:2b')

def test_pull_disk_floor_prevents_network(runtime,monkeypatch):
    monkeypatch.setattr('bughunter.local_media.shutil.disk_usage',lambda path:type('Disk',(),{'free':5*1024**3})())
    with pytest.raises(ValueError,match='5 GiB reserve'): runtime._pull('qwen3-vl:2b')

def test_verification_history_survives_model_switch(runtime,monkeypatch):
    monkeypatch.setattr(runtime,'vision',lambda *a,**k:'Visible facts: A red square on the left and blue circle on the right.\nUncertain or missing evidence: No audio.')
    runtime.test({}); runtime._test_thread.join(3)
    first=runtime._identity()
    runtime.configure({'vision_model':media_models.CURATED[0]['id']})
    assert not runtime.status()['vision_ready']
    runtime.configure({'vision_model':'qwen3-vl:2b'})
    assert runtime.status()['vision_ready']
    assert runtime._verification_key(first) in runtime.store.setting('media_verifications')

def test_second_runtime_preserves_live_and_legacy_running_jobs(tmp_path,monkeypatch):
    import os
    store=Store(tmp_path)
    monkeypatch.setattr(media_models,'discover',lambda:[])
    monkeypatch.setattr(LocalMediaRuntime,'_ollama_tags',lambda self:[])
    for owner in ({'owner_pid':os.getpid()}, {}):
        for key in ('media_setup_job','media_test_job'):
            store.set_setting(key,{'state':'running','detail':'Downloading 57%',**owner})
        second=LocalMediaRuntime(store)
        second.cancel_setup(); second.stop()
        assert store.setting('media_setup_job')['state']=='running'
        assert store.setting('media_test_job')['state']=='running'
        store.set_setting('media_setup_job',{'state':'ready','detail':'Download finished',**owner})
        store.set_setting('media_test_job',{'state':'ready','detail':'Image connection verified.',**owner})
        status=second.status()
        assert status['setup']['state']=='ready'
        assert status['test_job']['state']=='ready'

def test_restart_only_normalizes_dead_owner(tmp_path,monkeypatch):
    import os
    store=Store(tmp_path)
    store.set_setting('media_setup_job',{'state':'running','owner_pid':987654321,'detail':'Starting'})
    store.set_setting('media_test_job',{'state':'running','owner_pid':987654321,'detail':'Starting'})
    def dead(pid, signal): raise ProcessLookupError()
    monkeypatch.setattr(os,'kill',dead)
    runtime=LocalMediaRuntime(store)
    assert runtime._job['state']=='cancelled'
    assert runtime._test_job['state']=='cancelled'
    assert store.setting('media_setup_job')['state']=='cancelled'
    assert store.setting('media_test_job')['state']=='cancelled'

def test_second_runtime_cannot_start_over_live_job(runtime,monkeypatch):
    import os
    runtime.store.set_setting('media_setup_job',{'state':'running','owner_pid':os.getpid()})
    with pytest.raises(ValueError,match='Another app process'):
        runtime.setup({'target':'vision','approved':True,'model_id':'qwen3-vl:2b'})
    runtime.store.set_setting('media_test_job',{'state':'running','owner_pid':os.getpid()})
    with pytest.raises(ValueError,match='Another app process'): runtime.test({})

def test_independent_test_uses_override_without_changing_active_config(runtime,monkeypatch):
    original=runtime.config()
    chosen='huihui_ai/qwen3-vl-abliterated:4b-instruct'
    monkeypatch.setattr(media_models,'discover',lambda:[{'id':chosen,'local':True,'roles':['vision'],'digest':'sha:four'}])
    seen=[]
    def vision(*args,config,cancel_check,**kwargs):
        seen.append(dict(config))
        runtime.configure({'vision_provider':'codex_subscription','codex_vision_consent':True})
        cancel_check()  # An independent test must survive this selection change.
        return 'Visible facts: A red square on the left and blue circle on the right.\nUncertain or missing evidence: No audio.'
    monkeypatch.setattr(runtime,'vision',vision)
    response=runtime.test({'provider':'local','model':chosen})
    runtime._test_thread.join(3)
    assert response['model']==chosen
    assert seen[0]['vision_model']==chosen and seen[0]['vision_provider']=='local'
    assert runtime.config()['vision_model']==original['vision_model']
    assert runtime.config()['vision_provider']=='codex_subscription'
    assert runtime._test_job['state']=='ready'
    assert runtime.store.setting('media_verification')['model']==chosen

def test_test_override_validates_consent_and_capabilities(runtime,monkeypatch):
    with pytest.raises(ValueError,match='Allow sampled frames'):
        runtime.test({'provider':'codex_subscription','model':'gpt-6.1-sol'})
    with pytest.raises(ValueError): runtime.test({'provider':'unexpected','model':'model'})
    with pytest.raises(ValueError): runtime.test({'provider':'local','model':'../model'})
    monkeypatch.setattr(media_models,'discover',lambda:[{'id':'cloud-model','local':False,'roles':['vision'],'digest':'cloud'}])
    with pytest.raises(ValueError,match='capability-checked'):runtime.test({'provider':'local','model':'cloud-model'})

def test_default_test_cancels_when_settings_change(runtime,monkeypatch):
    def vision(*args,config,cancel_check,**kw):
        runtime.configure({'frame_interval':3})
        cancel_check()
    monkeypatch.setattr(runtime,'vision',vision)
    runtime.test({});runtime._test_thread.join(3)
    assert runtime._test_job['state']=='cancelled'
    assert runtime._test_job['model']=='qwen3-vl:2b'

def test_video_inference_uses_same_snapshot_as_provenance(runtime,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from pathlib import Path
    from bughunter.knowledge import KnowledgeService
    runtime.configure({'adaptive_frames':False,'frame_interval':2,'max_frames':2,'speech_enabled':False})
    service=KnowledgeService(runtime.store,start_worker=False);service.media_runtime=runtime
    source=runtime.store.put('source',{'title':'Snapshot lesson','status':'processing','content_hash':'snapshot-fixture'})
    artifacts=runtime.store.root/'artifacts'/source['id'];artifacts.mkdir()
    path=tmp_path/'lesson.mp4';path.write_bytes(b'fixture')
    seen=[]
    def run(args,timeout=1800):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'4'},'streams':[{'codec_type':'video','duration':'4'}]}))
        Path(args[-1]).write_bytes(b'frame');return SimpleNamespace(stdout='',stderr='',returncode=0)
    def vision(*args,config,**kwargs):
        seen.append(dict(config))
        runtime.configure({'vision_model':'qwen3-vl:4b'})
        return 'Visible controls at current timestamp; reusable lesson: compare visible evidence.'
    monkeypatch.setattr(service,'_run',run);monkeypatch.setattr(runtime,'vision',vision)
    monkeypatch.setattr(runtime,'checkpoint',lambda *args:None)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:name)
    try:
        segments,_=service._extract_media(path,artifacts,source)
        assert len(seen)==2 and all(config['vision_model']=='qwen3-vl:2b' for config in seen)
        assert all(item['model']=='qwen3-vl:2b' for item in segments if item['modality']=='vision')
    finally: service.shutdown()

def test_image_snapshot_matches_stored_provenance(runtime,monkeypatch,tmp_path):
    from PIL import Image
    from bughunter.knowledge import KnowledgeService
    image=tmp_path/'input.png';Image.new('RGB',(32,32),'blue').save(image)
    runtime.configure({'speech_enabled':False})
    service=KnowledgeService(runtime.store,start_worker=False);service.media_runtime=runtime
    source=runtime.store.put('source',{'title':'Image snapshot','status':'processing','path':str(image)})
    artifacts=runtime.store.root/'artifacts'/source['id'];artifacts.mkdir()
    seen=[]
    def vision(*args,config,**kwargs):
        seen.append(dict(config));runtime.configure({'vision_model':'qwen3-vl:4b'})
        return 'Visible facts: Blue image.\nUncertain or missing evidence: No narration.'
    monkeypatch.setattr(runtime,'vision',vision)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:None)
    try:
        segments,_=service._extract_image(source,artifacts)
        assert seen[0]['vision_model']=='qwen3-vl:2b'
        assert next(s for s in segments if s['modality']=='vision')['model']=='qwen3-vl:2b'
    finally:service.shutdown()

def test_vision_transport_obeys_explicit_snapshot(runtime,monkeypatch,tmp_path):
    from PIL import Image
    image=tmp_path/'transport.png';Image.new('RGB',(20,20),'red').save(image)
    snapshot={**runtime.config(),'vision_model':'qwen3-vl:4b'}
    runtime.configure({'vision_model':'qwen3-vl:8b'})
    sent=[];original=httpx.AsyncClient
    def handler(request):
        payload=json.loads(request.content);sent.append(payload)
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['vision']})
        return httpx.Response(200,json={'message':{'content':'Visible facts: Red image is visible.\nUncertain or missing evidence: No narration.'}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(runtime,'start',lambda *a,**kw:{'state':'running'})
    assert 'Red image' in runtime.vision(image,0,config=snapshot)
    assert all(payload['model']=='qwen3-vl:4b' for payload in sent)
    assert runtime.config()['vision_model']=='qwen3-vl:8b'

def test_local_test_starts_server_before_capability_check(runtime,monkeypatch):
    order=[];chosen='qwen3-vl:2b'
    def discover():
        order.append('inspect')
        if 'start' not in order:raise httpx.ConnectError('offline')
        return [{'id':chosen,'local':True,'roles':['vision'],'digest':'sha:two'}]
    monkeypatch.setattr(media_models,'discover',discover)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:'/test/ollama' if name=='ollama' else None)
    monkeypatch.setattr(runtime,'start',lambda **kwargs:order.append('start'))
    monkeypatch.setattr(runtime,'vision',lambda *a,**kw:'Visible facts: Red square on the left, blue circle on the right.\nUncertain or missing evidence: No audio.')
    runtime.test({'provider':'local','model':chosen});runtime._test_thread.join(3)
    assert order[:3]==['inspect','start','inspect']
    assert runtime._test_job['state']=='ready'

def test_timeout_error_has_actionable_detail(runtime,monkeypatch):
    def timeout(*args,**kw):raise httpx.ReadTimeout('')
    monkeypatch.setattr(runtime,'vision',timeout)
    runtime.test({});runtime._test_thread.join(3)
    assert runtime._test_job['state']=='error'
    assert 'timed out' in runtime._test_job['detail'] and '2B' in runtime._test_job['detail']

def test_low_memory_default_cached_ram_detection(monkeypatch):
    from types import SimpleNamespace
    media_models.low_memory_host.cache_clear();calls=[]
    monkeypatch.setattr(media_models.platform,'system',lambda:'Darwin')
    def detect(*args,**kwargs):calls.append(args);return SimpleNamespace(stdout=str(8*1024**3))
    monkeypatch.setattr(media_models.subprocess,'run',detect)
    try:
        assert media_models.low_memory_host() is True
        assert media_models.low_memory_host() is True
        assert len(calls)==1
    finally:media_models.low_memory_host.cache_clear()
