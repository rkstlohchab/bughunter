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
