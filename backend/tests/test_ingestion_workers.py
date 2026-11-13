"""Concurrent hosted sources preserve exclusive claims and local inference limits."""
import threading
import time
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.mark.parametrize('provider,expected',[('codex_subscription',2),('local',1)])
def test_worker_count_is_bounded_and_all_workers_close(tmp_path,provider,expected):
    runtime=SimpleNamespace(config=lambda:{'vision_provider':provider})
    service=KnowledgeService(Store(tmp_path),media_runtime=runtime)
    try:
        assert len(service._workers)==expected
        assert service.status()['worker_count']==expected
    finally:service.shutdown()
    assert not any(worker.is_alive() for worker in service._workers)


def test_two_hosted_imports_overlap_without_processing_a_source_twice(tmp_path,monkeypatch):
    store=Store(tmp_path)
    source_ids=[store.put('source',{'title':f'Course {index}','status':'queued'})['id']
                for index in range(2)]
    entered=threading.Barrier(2)
    calls=[]
    errors=[]
    def extract(service,source,*args):
        calls.append(source['id'])
        try:entered.wait(timeout=4)
        except threading.BrokenBarrierError:
            errors.append('Imports did not overlap')
            raise
        assert service._processing.source_id==source['id']
        return [{'text':f"Saved course evidence {source['id']}",'modality':'text'}],[]
    monkeypatch.setattr(KnowledgeService,'_extract',extract)
    runtime=SimpleNamespace(config=lambda:{'vision_provider':'codex_subscription'})
    service=KnowledgeService(store,media_runtime=runtime)
    try:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            if all(store.get('source',sid)['status']=='ready' for sid in source_ids):break
            time.sleep(.02)
        assert not errors
        assert sorted(calls)==sorted(source_ids)
        assert all(store.get('source',sid)['status']=='ready' for sid in source_ids)
        assert service.integrity()['status']=='healthy'
    finally:service.shutdown()


def test_storage_failure_does_not_kill_worker_or_restart_failed_extraction(tmp_path,monkeypatch):
    import os
    store=Store(tmp_path);service=KnowledgeService(store,start_worker=False)
    failed=store.put('source',{'title':'Storage failure','status':'queued'})
    healthy=store.put('source',{'title':'Next source','status':'queued'})
    calls=[]
    def process(id):
        calls.append(id)
        if id==failed['id']:
            store.put('source',{**failed,'status':'processing','worker_pid':os.getpid()})
            raise OSError(28,'No space left on device')
        store.put('source',{**healthy,'status':'ready'})
    monkeypatch.setattr(service,'process_source',process)
    worker=threading.Thread(target=service._work);service._workers=[worker]
    service._schedule(failed['id']);service._schedule(healthy['id']);worker.start()
    try:
        deadline=time.monotonic()+5
        while store.get('source',healthy['id'])['status']!='ready' and time.monotonic()<deadline:time.sleep(.02)
        assert worker.is_alive() and store.get('source',healthy['id'])['status']=='ready'
        result=store.get('source',failed['id']);assert result['status']=='error' and 'No space left' in result['error']
        assert result['worker_pid'] is None and result['last_failure']['error']==result['error']
        assert calls.count(failed['id'])==1
    finally:service.shutdown()


def test_worker_failure_cannot_overwrite_a_newer_cancellation(tmp_path,monkeypatch):
    store=Store(tmp_path);service=KnowledgeService(store,start_worker=False)
    source=store.put('source',{'title':'Cancelled source','status':'queued'})
    def process(id):
        store.put('source',{**source,'status':'cancelled','media_control':'cancel'})
        raise OSError(28,'No space left on device')
    monkeypatch.setattr(service,'process_source',process)
    worker=threading.Thread(target=service._work);service._workers=[worker];service._schedule(source['id']);worker.start()
    try:
        deadline=time.monotonic()+4
        while source['id'] in service._queued and time.monotonic()<deadline:time.sleep(.02)
        assert store.get('source',source['id'])['status']=='cancelled'
        assert worker.is_alive()
    finally:service.shutdown()
