"""Hermetic course checkpoint, audit and progressive retrieval regressions."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.media_sampling import scan_windows
from bughunter.store import Store


def test_window_offsets_gap_retry_and_threshold_identity(tmp_path):
    path = tmp_path / 'scenes.json'
    calls = []
    def run(args, timeout):
        start = float(args[args.index('-ss')+1]); calls.append(start)
        assert float(args[args.index('-t')+1]) <= 600
        if start == 600 and calls.count(start) == 1:
            raise subprocess.TimeoutExpired(args,timeout)
        return SimpleNamespace(returncode=0,stderr='pts_time:2.5 pts_time:9.5')
    progress = []
    times,gaps = scan_windows('ffmpeg','fixture',1205,path,{'source':'a'},run=run,progress=progress.append)
    assert times == [2.5,9.5,1202.5]
    assert [g['start'] for g in gaps] == [600]
    assert progress[-1]['total'] == 3 and progress[-1]['completed'] == 2
    times,gaps = scan_windows('ffmpeg','fixture',1205,path,{'source':'a'},run=run)
    assert calls == [0,600,1200,600] and not gaps
    assert 602.5 in times
    scan_windows('ffmpeg','fixture',1205,path,{'source':'a'},run=run,threshold=.4)
    assert len(calls) == 7


def test_scene_cancel_propagates_without_completion(tmp_path):
    def run(*args): raise InterruptedError('stop')
    with pytest.raises(InterruptedError):
        scan_windows('ffmpeg','fixture',600,tmp_path/'scan.json',{},run=run)
    assert not (tmp_path/'scan.json').exists()


def test_reject_retains_audit_and_removes_retrieval_and_assertions(tmp_path):
    service = KnowledgeService(Store(tmp_path),start_worker=False)
    source = service.store.put('source',{'title':'Course','status':'processing'})
    root = tmp_path/'artifacts'/source['id'];root.mkdir()
    evidence = {'text':'Unsupported magnetic coupon claim','locator':'Frame at 03:01:00',
                'modality':'vision','start':10860,'context_key':'first','provider':'local','model':'old'}
    service._publish_media(source,[evidence],root)
    chunk = service.search('magnetic coupon')[0]
    service.enrich(source['id'],[{'chunk_id':chunk['id'],'text':'Magnetic coupon lesson','quote':'magnetic coupon','kind':'technique'}])
    (root/'extracted.json').write_text('{}')
    assert service.reject(chunk['id'],'Unsupported visual interpretation')['status'] == 'rejected'
    assert not service.search('magnetic coupon')
    assert not any('magnetic' in n['label'] for n in service.graph()['nodes'])
    assert service.learnings()['total'] == 0
    assert service.get_source(source['id'])['chunks'][0]['active'] is False
    assert (root/('rejected-'+chunk['id']+'.json')).exists()
    assert not (root/'extracted.json').exists()
    assert json.loads((root/'media_timeline.json').read_text()) == []
    service._publish_media(source,[evidence],root)
    assert not service.search('magnetic coupon')


def test_new_generation_supersedes_old_but_preserves_corrections(tmp_path):
    service = KnowledgeService(Store(tmp_path),start_worker=False)
    source = service.store.put('source',{'title':'Course','status':'processing'})
    root = tmp_path/'artifacts'/source['id'];root.mkdir()
    old = {'text':'Original cobalt screenshot','modality':'vision','locator':'Frame at 00:00:00','context_key':'old'}
    service._publish_media(source,[old],root)
    first = service.search('cobalt')[0]
    service._publish_media(source,[{**old,'text':'Replacement amber screenshot','context_key':'new'}],root)
    assert not service.search('cobalt') and service.search('amber')
    service.correct(service.search('amber')[0]['id'],'Manual emerald correction')
    service._publish_media(source,[{**old,'text':'Generated crimson screenshot','context_key':'third'}],root)
    assert service.search('emerald') and not service.search('crimson')
    assert first['text'] in [c['original_text'] for c in service.get_source(source['id'])['chunks']]


def test_all_sections_of_fresh_generation_remain_active(tmp_path):
    service = KnowledgeService(Store(tmp_path),start_worker=False)
    source = service.store.put('source',{'title':'Course','status':'processing'})
    root = tmp_path/'artifacts'/source['id'];root.mkdir()
    text = '\n\n'.join(f'## {title}\n' + (f'{word} evidence from this screenshot. ' * 25) for title,word in
                        [('Visible facts','cobalt'),('Narrator says','emerald'),('Changes','amber'),('Lesson','crimson'),('Uncertainty','sapphire')])
    frame = {'text':text,'modality':'vision','locator':'Frame at 00:00:00','context_key':'first'}
    service._publish_media(source,[frame],root)
    assert len(service.search('evidence',limit=20)) == 5
    for word in ('cobalt','emerald','amber','crimson','sapphire'):
        assert service.search(word)
    service._publish_media(source,[frame],root)
    assert len(service.get_source(source['id'])['chunks']) == 5
    service._publish_media(source,[{**frame,'context_key':'second'}],root)
    assert len(service.search('evidence',limit=20)) == 5
    chunks = service.get_source(source['id'])['chunks']
    assert len(chunks)==10 and sum(c.get('active',True) for c in chunks)==5
    assert not service.integrity()['issues']


from test_media_speech import speech  # Reuse the hermetic WAV/Whisper fixture.


def test_completed_speech_chunk_is_searchable_before_next_chunk_fails(speech, monkeypatch):
    from bughunter.media_speech import transcribe_chunks
    service,source,model,artifacts,calls,invoke = speech
    source['title'] = 'Speech course'
    path = model.parent/'course.mp4'
    normal = service._run
    def run(args,timeout):
        if args[0]=='whisper' and '000001' in args[args.index('-of')+1]:
            assert service.search('Evidence')
            raise ValueError('second chunk failed')
        normal(args,timeout)
    monkeypatch.setattr(service,'_run',run)
    with pytest.raises(ValueError,match='second chunk'):
        transcribe_chunks(service,None,source,path,artifacts,1205,'ffmpeg','whisper',model,
                          publish=lambda completed:service._publish_media(source,completed,artifacts))
    assert service.search('Evidence')[0]['start']==1.25
    assert len(list(artifacts.rglob('chunk-*.srt')))==1


def test_audio_preparation_has_explicit_nonterminal_handoff(tmp_path,monkeypatch):
    service = KnowledgeService(Store(tmp_path),start_worker=False)
    source = service.store.put('source',{'title':'Course','status':'cancelled','path':str(tmp_path/'course.mp4')})
    seen = []
    def extract(path,root,source,*,audio_only):
        seen.append(audio_only)
        assert source['status']=='processing'
        assert source['media_control']=='run'
        return [],[]
    monkeypatch.setattr(service,'_extract_media',extract)
    result = service.prepare_audio(source['id'])
    assert seen==[True] and result['stage']=='audio_complete' and result['status']=='partial'
    service._update(source['id'],status='processing')
    with pytest.raises(ValueError,match='already processing'):service.prepare_audio(source['id'])


@pytest.mark.parametrize('failure',[False,True])
def test_detail_retry_is_bounded_cached_and_provider_failure_is_visible(tmp_path,monkeypatch,failure):
    service = KnowledgeService(Store(tmp_path/'db'),start_worker=False)
    source = service.store.put('source',{'title':'Course','status':'processing','content_hash':'fixture'})
    root=tmp_path/'artifacts';root.mkdir()
    video=tmp_path/'course.mp4';video.write_bytes(b'video fixture')
    calls=[]
    def run(args,timeout):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'2'},'streams':[{'codec_type':'video','duration':'2'}]}))
        from pathlib import Path
        Path(args[-1]).write_bytes(b'detail frame' if 'detail' in args[-1] else b'regular frame')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    def vision(path,stamp,**kwargs):
        calls.append(path.name)
        if failure:raise ValueError('provider unavailable')
        return 'Visible facts: Tiny text is unreadable.'
    settings={'enabled':True,'vision_enabled':True,'adaptive_frames':False,'frame_interval':2,'max_frames':1,'vision_provider':'local','vision_model':'fixture'}
    service.media_runtime=SimpleNamespace(config=lambda:settings,checkpoint=lambda *args:None,vision=vision)
    monkeypatch.setattr(service,'_run',run)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:name)
    if failure:
        with pytest.raises(ValueError,match='Visual analysis failed'):service._extract_media(video,root,source)
        assert (root/'media_timeline.json').exists()
        assert service.store.get('source',source['id'])['visual_failure']['timestamp']==0
        return
    service._extract_media(video,root,source)
    assert len(calls)==2 and calls[-1].endswith('-detail.jpg')
    service._extract_media(video,root,source)
    assert len(calls)==2
    frames=service.store.get('source',source['id'])['frames']
    assert frames[0]['artifact_id'].endswith('-detail.jpg')
