import json

from bughunter.knowledge import KnowledgeService
from bughunter.media_context import context_key
from bughunter.media_recovery import recover_hosted_observations, MODEL_CHANGE
from bughunter.store import Store


def test_recovery_checks_exact_evidence_and_preserves_rejected_audit(tmp_path):
    service=KnowledgeService(Store(tmp_path),start_worker=False)
    source=service.store.put('source',{'title':'Tutorial','status':'error','content_hash':'video'})
    root=tmp_path/'artifacts'/source['id'];root.mkdir()
    segments=[]
    for stamp in range(4):
        image=root/f'{stamp}.jpg';image.write_bytes(f'frame{stamp}'.encode())
        key=context_key('video','codex_subscription:gpt-6.1-sol',{'path':str(image),'time':stamp},[],[])
        segments.append({'text':f'Visible facts: Cobalt screenshot {stamp}.\nNarrator says: Absent.',
            'start':stamp,'locator':f'Frame {stamp}','frame_path':str(image),'modality':'vision',
            'provider':'codex_subscription','model':'gpt-6.1-sol','context_version':1,
            'context_key':key,'previous_frames':[],'narration_context':[]})
    try:
        service._publish_media(source,segments,root)
        chunks=service.get_source(source['id'])['chunks']
        service.reject(chunks[1]['id'],'Incorrect screen')
        with service.store.connection(immediate=True) as db:
            for chunk in (chunks[0],chunks[2],chunks[3]):
                row=db.execute('SELECT * FROM knowledge_chunks WHERE id=?',(chunk['id'],)).fetchone()
                reason=MODEL_CHANGE if chunk!=chunks[3] else 'Replaced by current frame evidence generation.'
                service._deactivate_chunk(db,row,'superseded',reason)
        (root/'2.jpg').write_bytes(b'changed evidence')
        (root/'media_timeline.json').write_text('[]')
        result=recover_hosted_observations(service,source['id'])
        assert result['recovered_observations']==1 and result['skipped_changed_evidence']==1
        found=service.search('Cobalt')
        assert len(found)==1 and found[0]['id']!=chunks[0]['id']
        current=service.get_source(source['id'])['chunks']
        assert len(current)==5 and sum(row.get('active',True) for row in current)==1
        assert next(row for row in current if row['id']==chunks[1]['id'])['review_status']=='rejected'
        assert recover_hosted_observations(service,source['id'])['recovered_observations']==0
        assert service.integrity()['status']=='healthy'
    finally:service.shutdown()
