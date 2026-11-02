"""P2 deterministic source monitoring and split-leakage regression tests."""
import base64
import json
import zipfile

import pytest

from bughunter.store import Store
from bughunter.knowledge import KnowledgeService
from bughunter.research_p2 import ResearchP2, parse_entries, validate_public_url
from bughunter.dataset_p2 import DatasetsP2


@pytest.fixture
def services(tmp_path,monkeypatch):
    store=Store(tmp_path/'db')
    knowledge=KnowledgeService(store,start_worker=False)
    monkeypatch.setattr(knowledge,'_index_vectors',lambda source:'disabled')
    yield store,knowledge
    knowledge.shutdown()


def finish_source(knowledge,text):
    source=knowledge.import_source(filename='lesson.md',content_base64=base64.b64encode(text.encode()).decode())
    record=knowledge.process_source(source['id'])
    assert record['status'] in ('ready','partial')
    with knowledge.store.connection() as db:
        chunk=db.execute('SELECT id,source_id,locator,original_text FROM knowledge_chunks WHERE source_id=? LIMIT 1',(source['id'],)).fetchone()
    assert chunk
    return source,chunk


def test_parses_atom_and_rejects_unsafe_urls(monkeypatch):
    monkeypatch.setattr('bughunter.research_p2.socket.getaddrinfo',lambda *args,**kwargs: [(None,None,None,None,('93.184.215.14',443))])
    assert validate_public_url('https://example.com/releases')
    for url in ['http://example.com','https://localhost/private','https://10.0.0.1/','https://anything.onion','https://example.com:8443','https://user:pass@example.com']:
        with pytest.raises(ValueError):validate_public_url(url)
    xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>one</id><title>Release</title><summary>New feature</summary><link href="https://example.com/post"/></entry></feed>'
    assert parse_entries(xml,'rss','https://example.com/rss')[0]['title']=='Release'
    with pytest.raises(Exception):parse_entries(b'<!DOCTYPE x [<!ENTITY xx SYSTEM "file:///etc/passwd">]><rss><item>&xx;</item></rss>','rss','https://example.com/rss')


def test_new_changed_review_and_no_automatic_knowledge(services,monkeypatch):
    store,knowledge=services
    monkeypatch.setattr('bughunter.research_p2.validate_public_url',lambda url:url)
    feeds=[b'<rss><channel><item><guid>p1</guid><title>Release</title><description>Version one</description></item></channel></rss>',
           b'<rss><channel><item><guid>p1</guid><title>Release</title><description>Version two</description></item></channel></rss>']
    service=ResearchP2(store,knowledge,fetcher=lambda u,k:feeds.pop(0))
    watch=service.add('https://example.org/feed','Release notes')
    assert not watch['enabled']
    first=service.check(watch['id'])
    assert first['new_count']==1
    assert not store.list('source')
    second=service.check(watch['id'])
    assert second['new_count']==1
    candidates=service.inbox()
    assert {c['reason'] for c in candidates}=={'new','changed'}
    source=service.review(candidates[0]['id'],accept=True)
    assert source['status']=='accepted' and source['source_id']
    assert len(store.list('source'))==1
    assert service.review(candidates[1]['id'],accept=False)['status']=='dismissed'
    assert not service.inbox()


def test_monitor_limits_and_error_preserves_state(services,monkeypatch):
    store,knowledge=services
    monkeypatch.setattr('bughunter.research_p2.validate_public_url',lambda url:url)
    runner=ResearchP2(store,knowledge,fetcher=lambda u,k: (_ for _ in ()).throw(ValueError('upstream unavailable')))
    watch=runner.add('https://example.org/feed','source')
    with pytest.raises(ValueError,match='upstream'):
        runner.check(watch['id'])
    assert runner._watch(watch['id'])['status']=='error'
    with pytest.raises(ValueError,match='already subscribed'):
        runner.add('https://example.org/feed','another')
    with pytest.raises(ValueError): runner.update(watch['id'],interval_hours=1)


def test_dataset_export_and_source_provenance(services):
    store,knowledge=services
    source,chunk=finish_source(knowledge,'Step 1: Verify a local experiment.\nThen record its output.')
    a=knowledge.portability().save_example(chunk['id'],'How do we verify the test?','Check the returned output.',reviewed=True,training_allowed=True,split='train')
    result=DatasetsP2(store)
    assert result.inspect()['counts']=={'train':1,'evaluation':0}
    archive=result.export()
    with zipfile.ZipFile(archive['path']) as z:
        assert len(z.read('train.jsonl').splitlines())==1
        assert z.read('evaluation.jsonl')==b''
        assert 'source_id' in z.read('provenance.jsonl').decode()
        assert json.loads(z.read('manifest.json'))['holdout_present'] is False
    with store.connection(immediate=True) as db:
        db.execute('UPDATE knowledge_chunks SET original_text=? WHERE id=?',('changed',chunk['id']))
    assert result.inspect()['exclusion_reasons']['citation_changed_or_missing']==1
    with pytest.raises(ValueError,match='No reviewed'):
        result.export()


def test_dataset_leakage_and_dedup(services):
    store,knowledge=services
    a,c=finish_source(knowledge,'Step 1: Check the output.\nThen collect the evidence.')
    # Source-consistent train and independent evaluation set: different sources.
    train=store.put('learning_example',{'question':'Verify the task','answer':'Check result','source_id':a['id'],'chunk_id':c['id'],
        'locator':c['locator'],'supporting_text':c['original_text'],'reviewed':True,'training_allowed':True,'split':'train'})
    eval=store.put('learning_example',{'question':'Verify the task!','answer':'Check result again', 'source_id':a['id'],'chunk_id':c['id'],
        'locator':c['locator'],'supporting_text':c['original_text'],'reviewed':True,'training_allowed':True,'split':'evaluation'})
    info=DatasetsP2(store).inspect()
    assert info['eligible']==0 and info['exclusion_reasons']['split_leakage_conflict']==2
    assert not info['independent_evaluation_available']


def test_separate_sources_can_hold_out(services):
    store,knowledge=services
    first,c1=finish_source(knowledge,'Step 1: Inspect sample A.\nThen record the output.')
    second=knowledge.import_source(filename='another.md',content_base64=base64.b64encode(b'Step 1: Inspect sample B.\nThen record the result.').decode())
    knowledge.process_source(second['id'])
    with store.connection() as db:
        c2=db.execute('SELECT * FROM knowledge_chunks WHERE source_id=? LIMIT 1',(second['id'],)).fetchone()
    for i,(source,chunk,split) in enumerate([(first,c1,'train'),(second,c2,'evaluation')]):
        store.put('learning_example',{'question':'Explain lesson '+str(i),'answer':'Observed result '+str(i),'source_id':source['id'],
            'chunk_id':chunk['id'],'locator':chunk['locator'],'supporting_text':chunk['original_text'],
            'reviewed':True,'training_allowed':True,'split':split})
    info=DatasetsP2(store).inspect()
    assert info['counts']=={'train':1,'evaluation':1} and info['dataset_ready']


def test_training_workspace_requires_explicit_new_permission(services):
    store,_=services
    e=store.put('training_example',{'status':'reviewed','instruction':'Question','response':'Answer','source':'self:manual','group':'g'})
    dataset=DatasetsP2(store)
    assert dataset.inspect()['eligible']==0
    dataset.consent(e['id'],True)
    assert dataset.inspect()['eligible']==1
    dataset.consent(e['id'],False)
    assert dataset.inspect()['eligible']==0


def test_training_source_is_real_or_explicitly_manual(services):
    store,_=services
    example=store.put('training_example',{'status':'reviewed','instruction':'Q','response':'A',
        'source':'invented document','training_allowed':True,'group':'x'})
    audit=DatasetsP2(store).inspect()
    assert audit['eligible']==0 and audit['exclusion_reasons']['missing_source_reference']==1


def test_question_leakage_across_different_source_documents(services):
    store,knowledge=services
    first,c1=finish_source(knowledge,'Step 1: Observe A. Then verify output.')
    second=knowledge.import_source(filename='other.md', content_base64=base64.b64encode(b'Step 1: Observe B. Then verify output.').decode())
    knowledge.process_source(second['id'])
    with store.connection() as db:
        c2=db.execute('SELECT * FROM knowledge_chunks WHERE source_id=? LIMIT 1',(second['id'],)).fetchone()
    for source,chunk,split in [(first,c1,'train'),(second,c2,'evaluation')]:
        store.put('learning_example',{'question':'What is the exact same question?','answer':'Same answer','source_id':source['id'],
            'chunk_id':chunk['id'],'locator':chunk['locator'],'supporting_text':chunk['original_text'],
            'reviewed':True,'training_allowed':True,'split':split})
    report=DatasetsP2(store).inspect()
    assert report['eligible']==0
    assert report['exclusion_reasons']['split_leakage_conflict']==2
