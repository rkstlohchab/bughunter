"""Evidence counts never claim exhaustive video coverage or practical mastery."""
import json

from bughunter.knowledge import KnowledgeService
from bughunter.media_quality import coverage
from bughunter.store import Store


def test_sampled_video_report_counts_only_active_evidence_and_keeps_exact_quotes(tmp_path):
    service=KnowledgeService(Store(tmp_path),start_worker=False)
    source=service.store.put('source',{'title':'Tutorial','status':'processing','filename':'tutorial.mp4',
        'duration':120,'speech_coverage':{'completed':1,'total':1,'seconds':120,'duration':120},
        'sampling_plan':{'duration':120,'planned':[0,30,60,90],'interval':30}})
    root=tmp_path/'artifacts'/source['id'];root.mkdir()
    segments=[{'text':'Open Burp Suite and select Extensions.','modality':'transcript','start':0,'end':30,'locator':'00:00:00–00:00:30'},
        {'text':'The next phrase is (inaudible).','modality':'transcript','start':30,'end':60,'locator':'00:00:30–00:01:00'},
        *[{'text':f'Visible facts: Select the panel {index}.\nUncertain or missing evidence: '+('Text is unreadable.' if index==2 else 'No action proven.'),
            'modality':'vision','start':index*30,'locator':f'Frame {index}',
            'context_key':f'key{index}','provider':'claude_subscription','model':'claude-sonnet-4-6'} for index in range(4)],
        {'text':'Burp Suite','modality':'ocr','start':0,'locator':'OCR at zero'}]
    try:
        service._publish_media(source,segments,root)
        rows=service.get_source(source['id'])['chunks']
        rejected=next(row for row in rows if row['locator']=='Frame 1')
        superseded=next(row for row in rows if row['locator']=='Frame 3')
        service.reject(rejected['id'],'Contradicted by frame')
        with service.store.connection(immediate=True) as db:
            row=db.execute('SELECT * FROM knowledge_chunks WHERE id=?',(superseded['id'],)).fetchone()
            service._deactivate_chunk(db,row,'superseded','New generation')
        before=service.store.get('source',source['id'])
        result=coverage(service,source['id'])
        assert result['audio']['transcription_process_complete']
        assert result['audio']['unclear_total']==1
        assert result['visual']['planned_frames']==4
        assert result['visual']['completed_planned_frames']==2
        assert not result['visual']['all_planned_samples_saved']
        assert result['visual']['maximum_sampling_spacing_seconds']==30
        assert result['visual']['unreadable_total']==1
        assert len(result['visual']['pending_ranges'])==2
        assert result['methods']['independently_tested'] is False
        assert all(value is False for value in result['assurance'].values() if isinstance(value,bool))
        assert result['methods']['mentioned_tools'][0]['name']=='Burp Suite'
        with service.store.connection() as db:
            for section in [result['methods']['candidate_steps'],result['visual']['unreadable_intervals'],result['audio']['unclear_passages']]:
                for item in section:
                    chunk=db.execute('SELECT text,metadata FROM knowledge_chunks WHERE id=?',(item['chunk_id'],)).fetchone()
                    assert item['quote'] in chunk['text'] and json.loads(chunk['metadata']).get('active',True)
        assert service.store.get('source',source['id'])==before
    finally:service.shutdown()


def test_old_frame_count_cannot_prove_exact_plan_or_all_actions(tmp_path):
    service=KnowledgeService(Store(tmp_path),start_worker=False)
    try:
        source=service.store.put('source',{'title':'Old import','filename':'old.mp4','status':'ready','duration':600,
            'media_progress':{'phase':'frames','analyzed':20,'total':20}})
        result=coverage(service,source['id'])
        assert not result['visual']['plan_available'] and result['visual']['planned_frames']==20
        assert result['visual']['completed_planned_frames'] is None
        assert not result['visual']['all_planned_samples_saved']
        assert result['windows'][0]['status']=='plan_unavailable'
        assert not result['assurance']['all_actions_captured']
    finally:service.shutdown()


def test_document_report_exposes_warnings_without_implying_training(tmp_path):
    service=KnowledgeService(Store(tmp_path),start_worker=False)
    try:
        source=service.store.put('source',{'title':'Slides','filename':'slides.pdf','status':'partial',
            'warnings':['Page 6 has no extracted text.']})
        result=coverage(service,source['id'])
        assert result['kind']=='document' and not result['windows']
        assert result['warnings']==['Page 6 has no extracted text.']
        assert result['methods']['candidate_steps_total']==0
        assert not result['assurance']['model_weights_trained']
    finally:service.shutdown()
