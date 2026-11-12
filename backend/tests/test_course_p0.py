"""P0 course import & procedural evidence tests; no model/network dependency."""
import json
import stat
import time
import zipfile
from pathlib import Path

import pytest

from bughunter.course_p0 import _safe_items, ProcedureP0, CourseP0
from bughunter.media_sampling import plan_frames, scene_cuts
from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store=Store(tmp_path/'data')
    k=KnowledgeService(store,start_worker=False)
    monkeypatch.setattr(k,'_index_vectors',lambda source:'disabled')
    yield k
    k.shutdown()


def archive(tmp_path):
    path=tmp_path/'course.zip'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('01 - Introduction.md', 'Step 1: Open the offline test program.\nStep 2: Click the run button.\nFinally verify its output.')
        z.writestr('02 - Scan.py', '# source text\nprint("hello")')
        z.writestr('empty.exe', b'MZ'+b'\x00'*64)
    return path


def finish(k,batch_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        b=k.courses.status(batch_id)
        if b['status'] in {'complete','partial','interrupted'}:return b
        time.sleep(.025)
    raise AssertionError('Course import did not finish')


def test_course_zip_is_resumable_and_per_member_sources(setup,tmp_path):
    k=setup
    batch=k.courses.import_archive(str(archive(tmp_path)))
    result=finish(k,batch['id'])
    assert result['status']=='complete', result.get('errors')
    assert result['imported_sources']==2
    assert any(i['state']=='skipped' for i in result['items'])
    duplicate=k.courses.import_archive(str(tmp_path/'course.zip'))
    assert duplicate['duplicate'] is True
    assert len(k.courses.list_batches())==1
    ids=[i['source_id'] for i in result['items'] if i.get('source_id')]
    assert len(ids)==2
    assert all(k.store.get('source',id)['course_batch_id']==batch['id'] for id in ids)
    for id in ids:
        assert k.process_source(id)['status'] in ('ready','partial')
    assert any('hello' in row['text'] for id in ids for row in k.get_source(id)['chunks'])
    assert any('Step 1' in row['text'] for id in ids for row in k.get_source(id)['chunks'])
    assert k.procedures.list(source_id=ids[0]) or k.procedures.list(source_id=ids[1])
    k.courses._update(batch['id'],status='interrupted')
    k.courses.run_archive(batch['id'])
    assert k.courses.status(batch['id'])['imported_sources']==2


def test_procedures_cite_verbatim_source_and_are_unverified(setup,tmp_path):
    k=setup
    result=finish(k,k.courses.import_archive(str(archive(tmp_path)))['id'])
    src=next(i['source_id'] for i in result['items'] if i['member'].endswith('.md'))
    k.process_source(src)
    procedures=k.procedures.list(source_id=src)
    assert procedures
    doc=k.get_source(src)
    lookup={row['id']:row for row in doc['chunks']}
    for proc in procedures:
        assert proc['status']=='candidate' and proc['verified'] is False
        for step in proc['steps']:
            assert step['quote'] in lookup[step['chunk_id']]['text']
            assert step['locator']==lookup[step['chunk_id']]['locator']
            assert step['verified'] is False
    graph=k.graph(limit=1000)
    assert any(edge['relation']=='step_evidence' for edge in graph['edges'])
    exported=k.portability().export_bundle(include_frames=False)
    with zipfile.ZipFile(exported['path']) as zipf:
        rows=[json.loads(line) for line in zipf.read('candidate_procedures.jsonl').splitlines()]
        assert any(p['source_id']==src for p in rows)
        assert zipf.read('train.jsonl') == b''
    k.delete_source(src)
    assert not k.procedures.list(source_id=src)


def test_zip_slip_and_symlink_rejected(setup,tmp_path):
    for name in ('../evil.md','/absolute.md','C:/drive.md','a\\b.md'):
        path=tmp_path/'bad.zip'
        with zipfile.ZipFile(path,'w') as zipf:zipf.writestr(name,'test')
        with pytest.raises(ValueError): setup.courses.import_archive(str(path))
    path=tmp_path/'link.zip'
    with zipfile.ZipFile(path,'w') as zipf:
        item=zipfile.ZipInfo('link.md')
        item.create_system=3
        item.external_attr=(stat.S_IFLNK|0o777)<<16
        zipf.writestr(item,'target')
    with pytest.raises(ValueError): setup.courses.import_archive(str(path))


def test_frame_sampling_includes_scene_boundaries_and_cap():
    assert plan_frames(60,12,0,[5,25,59])==[0,5,12,24,36,48,59]
    assert len(plan_frames(600,12,12,[3,5,7]))<=12
    assert plan_frames(0,12)==[0.0]
    with pytest.raises(ValueError):plan_frames(100,1)
    assert scene_cuts('/missing-ffmpeg','/missing.mp4',50)==[]


def test_no_automatic_method_verification(setup):
    k=setup
    source=k.import_source(filename='lesson.md',content_base64='U2VsZWN0IHRoZSBtZW51IGFuZCBjbGljayBTYXZlLg==')
    k.process_source(source['id'])
    candidates=k.procedures.list(source_id=source['id'])
    assert all(p['status']=='candidate' and p['verified'] is False for p in candidates)


def test_repeated_course_instructions_have_unique_stable_procedure_ids(setup):
    import base64
    k=setup
    lesson='\n'.join(['Click the test button and verify the output.']*14)
    source=k.import_source(filename='repeated.md',content_base64=base64.b64encode(lesson.encode()).decode())
    result=k.process_source(source['id'])
    assert result['status']=='ready',result.get('warnings')
    procedures=k.procedures.list(source_id=source['id'])
    assert len(procedures)==3
    assert sum(len(p['steps']) for p in procedures)==14
    assert len({p['id'] for p in procedures})==3
    before={p['id']:p['steps'] for p in procedures}
    k.procedures.build(source['id'])
    assert {p['id']:p['steps'] for p in k.procedures.list(source_id=source['id'])}==before


@pytest.mark.parametrize('unreadable',[False,True])
def test_repair_keeps_real_gaps_and_preserves_citations_without_original(setup,monkeypatch,unreadable):
    from bughunter.knowledge import MEDIA_SAMPLING_NOTE
    k=setup
    source=k.import_source(filename='lesson.md',content_base64='U2VsZWN0IHRoZSBtZW51IGFuZCBjbGljayBTYXZlLg==')
    k.process_source(source['id'])
    before=k.get_source(source['id'])['chunks']
    warnings=[MEDIA_SAMPLING_NOTE,'Procedure extraction unavailable: UNIQUE constraint failed: entities.id']
    if unreadable:warnings.append('Skipped an unreadable frame at 00:03:00.')
    k._update(source['id'],status='partial',warnings=warnings,original_retained=False)
    monkeypatch.setattr(k,'_extract',lambda *_args:pytest.fail('Repair must reuse saved chunks'))
    repaired=k.rebuild_procedures(source['id'])
    row=k.get_source(source['id'])
    assert repaired['source_status']==('partial' if unreadable else 'ready')
    assert row['extraction_notes']==[MEDIA_SAMPLING_NOTE]
    assert row['warnings']==(['Skipped an unreadable frame at 00:03:00.'] if unreadable else [])
    assert row['chunks']==before and row['original_retained'] is False
    assert k.procedures.list(source_id=source['id'])
    k.rebuild_procedures(source['id'])
    assert k.get_source(source['id'])['extraction_notes']==[MEDIA_SAMPLING_NOTE]


def test_sampling_note_does_not_mark_successful_ingestion_partial(setup,monkeypatch):
    from bughunter.knowledge import MEDIA_SAMPLING_NOTE
    k=setup
    source=k.import_source(filename='lesson.md',content_base64='U2VsZWN0IHRoZSBtZW51IGFuZCBjbGljayBTYXZlLg==')
    monkeypatch.setattr(k,'_extract',lambda *_args:([{'text':'Select the menu and click Save.','locator':'Frame at 00:00:00','modality':'vision'}],[MEDIA_SAMPLING_NOTE]))
    result=k.process_source(source['id'])
    assert result['status']=='ready' and result['warnings']==[]
    assert result['extraction_notes']==[MEDIA_SAMPLING_NOTE]


@pytest.mark.parametrize('stream_duration,expected_end', [('6',6),('N/A',12),(None,12),('nan',12)])
def test_frames_stop_at_video_duration_and_keep_audio_tail(setup,tmp_path,monkeypatch,stream_duration,expected_end):
    from types import SimpleNamespace
    from bughunter.knowledge import MEDIA_AUDIO_TAIL_NOTE, extraction_messages
    from bughunter.local_media import LocalMediaRuntime
    k=setup
    source=k.store.put('source',{'id':'video_tail','title':'Audio tail','status':'processing'})
    runtime=LocalMediaRuntime(k.store)
    runtime.configure({'frame_interval':2,'max_frames':0})
    k.media_runtime=runtime
    artifacts=tmp_path/'frames'
    artifacts.mkdir()
    speech=[{'text':'Last spoken instruction.','locator':'00:00:11','modality':'transcript','start':11,'end':12}]
    sampled=[]
    scene_bounds=[]
    def fake_run(args,timeout=1800):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'12'},'streams':[
                {'codec_type':'video','duration':stream_duration},{'codec_type':'audio','duration':'12'}]}))
        sampled.append(float(args[args.index('-ss')+1]))
        assert args[args.index('-map')+1]=='0:v:0'
        Path(args[-1]).write_bytes(b'frame fixture')
        return SimpleNamespace(stdout='')
    def cuts(_ffmpeg,_path,duration,**_kwargs):
        scene_bounds.append(duration)
        return [1,7,11]
    monkeypatch.setattr(k,'_run',fake_run)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:name)
    monkeypatch.setattr('bughunter.media_sampling.scene_cuts',cuts)
    monkeypatch.setattr(runtime,'checkpoint',lambda *_args:None)
    monkeypatch.setattr(runtime,'vision',lambda _path,stamp,**kwargs:f'Frame at {stamp}')
    segments,notes=k._extract_media(tmp_path/'lesson.mp4',artifacts,source,transcript=speech)
    assert scene_bounds==[expected_end]
    assert sampled and all(t<expected_end for t in sampled)
    assert max(s['end'] for s in segments if s['modality']=='vision')==expected_end
    assert speech[0] in segments
    assert speech==[{'text':'Last spoken instruction.','locator':'00:00:11','modality':'transcript','start':11,'end':12}]
    row=k.store.get('source',source['id'])
    assert row['duration']==12 and row['video_duration']==expected_end
    assert row['media_progress']['analyzed']==row['media_progress']['total']==len(sampled)
    warnings,informational=extraction_messages(notes)
    assert warnings==[]
    assert (MEDIA_AUDIO_TAIL_NOTE in informational)==(expected_end<12)
