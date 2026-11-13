"""Desktop RPC integration for evidence withdrawal and emergency cancellation."""
import threading
import time

import pytest
from PIL import Image

from bughunter.app import Application


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance=Application(tmp_path, primary=False)
    monkeypatch.setattr(instance.media, '_ollama_tags', lambda: [])
    yield instance
    instance.shutdown()


def test_withdrawn_screen_cannot_supply_a_reusable_procedure(app):
    source=app.store.put('source', {'title':'Screen tutorial','status':'processing'})
    root=app.store.root/'artifacts'/source['id']; root.mkdir()
    app.knowledge._publish_media(source,[{
        'text':'1. Open the account menu.\n2. Verify the response status.',
        'locator':'Frame at 00:01:00','start':60,'modality':'vision',
        'context_key':'old-generation',
    }],root)
    chunk=app.knowledge.get_source(source['id'])['chunks'][0]
    app.knowledge.procedures.build(source['id'])
    procedure=app.knowledge.procedures.list(source_id=source['id'])[0]
    result=app.dispatch('knowledge.reject',{'id':chunk['id'],'reason':'The screen was misidentified.'})
    assert result['status']=='rejected'
    assert not app.knowledge.search('account menu')
    assert not app.knowledge.procedures.list(source_id=source['id'])
    with pytest.raises(ValueError,match='withdrawn'):
        app.knowledge.procedures.get(procedure['id'])
    assert procedure['id'] not in {node['id'] for node in app.knowledge.graph()['nodes']}
    assert app.knowledge.procedures.build(source['id'])['procedures']==0
    assert app.knowledge.get_source(source['id'])['chunks'][0]['text']==chunk['text']


def test_regular_reindex_cannot_restore_withdrawn_generated_description(app):
    source=app.store.put('source',{'title':'Imported screenshot','status':'processing'})
    segments=[{'text':'Open the incorrect window and verify the invented result.',
               'modality':'vision','locator':'Visual analysis'}]
    app.knowledge._index(source,segments)
    chunk=app.knowledge.get_source(source['id'])['chunks'][0]
    app.dispatch('knowledge.reject',{'id':chunk['id'],'reason':'The screenshot contradicts this description.'})
    assert app.knowledge._index(source,segments)==0
    assert not app.knowledge.search('invented result')
    record=app.knowledge.get_source(source['id'])['chunks'][0]
    assert record['active'] is False and record['review_status']=='rejected'
    assert record['original_text']==chunk['text']
    replacement=[{**segments[0],'text':'The browser shows a GitHub README and Python extension settings.'}]
    assert app.knowledge._index(source,replacement)==1
    rows=app.knowledge.get_source(source['id'])['chunks']
    assert len(rows)==2 and next(r for r in rows if r['id']==chunk['id'])['active'] is False


def test_image_reanalysis_preserves_a_corrected_passage(app):
    source=app.store.put('source',{'title':'Imported screenshot','status':'processing'})
    old=[{'text':'Open the menu on the left.','modality':'vision','locator':'Visual analysis'}]
    app.knowledge._index(source,old)
    chunk=app.knowledge.get_source(source['id'])['chunks'][0]
    app.dispatch('knowledge.correct',{'id':chunk['id'],'text':'The menu is on the right.'})
    assert app.knowledge._index(source,[{**old[0],'text':'The new model describes a different panel.'}])==2
    rows=app.knowledge.get_source(source['id'])['chunks']
    saved=next(r for r in rows if r['id']==chunk['id'])
    assert saved['corrected'] and saved['text']=='The menu is on the right.'
    assert app.knowledge.search('menu right')


def test_resumed_frames_preserve_registered_evidence_and_manual_images(app):
    source=app.store.put('source',{'title':'Tutorial','status':'processing',
        'frames':[{'path':'/original.jpg','artifact_id':'original.jpg','time':600}],
        'frame_count':1})
    app.knowledge._register_media_frames(source['id'],[{'path':'/current.jpg','artifact_id':'current.jpg','time':0}])
    app.knowledge._register_media_frames(source['id'],[{'path':'/current.jpg','artifact_id':'current.jpg','time':0}])
    saved=app.store.get('source',source['id'])
    assert saved['frame_count']==2
    assert [frame['time'] for frame in saved['frames']]==[0,600]


def test_recovery_has_the_media_runtime_before_scheduling(app, monkeypatch):
    from bughunter.knowledge import KnowledgeService
    checked=[]
    def resume(service):
        checked.append(service.media_runtime)
    monkeypatch.setattr(KnowledgeService,'resume_pending',resume)
    service=KnowledgeService(app.store,start_worker=True,media_runtime=app.media)
    try:
        assert checked and checked[0] is app.media
    finally:
        service.shutdown()


def test_desktop_stop_cancels_source_image_test_without_switching_chat(app, monkeypatch):
    before=app.settings.get()
    source=app.store.put('source',{'title':'Tutorial','status':'ready'})
    root=app.store.root/'artifacts'/source['id']; root.mkdir()
    frame=root/'test.png'; Image.new('RGB',(8,8),'blue').save(frame)
    app.store.put('source',{**source,'frames':[{'artifact_id':frame.name,'path':str(frame),'time':0}]})
    app.dispatch('media.configure',{'vision_provider':'codex_subscription','codex_vision_consent':True})
    entered=threading.Event()
    def running_vision(*args,**kwargs):
        entered.set()
        while True:
            kwargs['cancel_check']()
            time.sleep(.01)
    monkeypatch.setattr(app.media,'vision',running_vision)
    assert app.dispatch('media.test',{'source_id':source['id'],'artifact_id':frame.name})['state']=='started'
    assert entered.wait(2)
    result=app.dispatch('system.stop')
    assert result['stopped']
    deadline=time.monotonic()+2
    while app.media.status()['test_job']['state']=='running' and time.monotonic()<deadline:
        time.sleep(.02)
    assert app.media.status()['test_job']['state']=='cancelled'
    assert not app.media.status()['vision_ready']
    assert app.settings.get()==before
    app.dispatch('system.resume')
    assert not app.store.setting('emergency_stop')
    assert app.media.status()['test_job']['state']=='cancelled'
