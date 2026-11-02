import base64
import json
from pathlib import Path
import zipfile

import pytest

from bughunter.knowledge import KnowledgeService, MAX_FILE_BYTES
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path,monkeypatch):
    service = KnowledgeService(Store(tmp_path/'data'),start_worker=False)
    monkeypatch.setattr(service,'_index_vectors',lambda id:'unavailable')
    yield service
    service.shutdown()


def lesson(knowledge):
    source = knowledge.import_source(filename='course.md',content_base64=base64.b64encode(b'Coupon eligibility must be enforced by the server.').decode())
    knowledge.process_source(source['id'])
    return knowledge.get_source(source['id'])


def test_release_preserves_search_graph_corrections_and_export(knowledge):
    source = lesson(knowledge)
    chunk = source['chunks'][0]
    knowledge.correct(chunk['id'],'Coupon eligibility must be enforced on the server, including excluded products.')
    archive = knowledge.portability()
    example = archive.save_example(chunk['id'],'Where should eligibility be enforced?','On the server.',True,True)
    archive.save_example(chunk['id'],'Unreviewed question','Unreviewed answer',False,True)
    archive.save_example(chunk['id'],'Held-out question','Held-out answer',True,True,'evaluation')
    with pytest.raises(ValueError,match='another split'):
        archive.save_example(chunk['id'],'where SHOULD eligibility be enforced?','Anything',True,True,'evaluation')
    raw = Path(source['path']); artifacts = knowledge.store.root/'artifacts'/source['id']
    (artifacts/'audio.wav').write_bytes(b'audio')
    size = raw.stat().st_size
    assert archive.release_original(source['id'])['released_bytes'] >= size
    assert not raw.exists() and not (artifacts/'audio.wav').exists()
    assert (artifacts/'extracted.json').exists()
    assert knowledge.search('excluded products')
    assert knowledge.graph()['nodes']
    with pytest.raises(ValueError,match='original was released'): knowledge.retry_source(source['id'])
    result = archive.export_bundle(include_frames=False)
    with zipfile.ZipFile(result['path']) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['schema_version']==1 and not manifest['contains_model_weights']
        passages = [json.loads(line) for line in z.read('passages.jsonl').decode().splitlines()]
        assert passages[0]['corrected'] and 'excluded' in passages[0]['text']
        train = [json.loads(line) for line in z.read('train.jsonl').decode().splitlines()]
        assert len(train)==1 and train[0]['metadata']['example_id']==example['id']
        assert len(z.read('evaluation.jsonl').decode().splitlines())==1
        assert len(z.read('corrections.jsonl').decode().splitlines())==1
        assert source['path'] not in z.read('sources.jsonl').decode()
    # Restore extraction only when the original is explicitly imported again.
    restored = knowledge.import_source(filename='course.md',content_base64=base64.b64encode(b'Coupon eligibility must be enforced by the server.').decode())
    assert restored['id']==source['id'] and restored['status']=='queued'
    knowledge.delete_source(source['id'])
    assert knowledge.store.list('learning_example')==[]


def test_linked_files_are_never_deleted_and_partial_requires_review(knowledge,tmp_path):
    original = tmp_path/'course.txt'; original.write_text('Coupon eligibility from a course.')
    source = knowledge.import_source(path=str(original),copy_original=False)
    assert source['path']==str(original) and not list((knowledge.store.root/'sources').iterdir())
    knowledge.process_source(source['id'])
    knowledge._update(source['id'],status='partial',warnings=['Missing visual demonstration'])
    with pytest.raises(ValueError,match='Review'): knowledge.portability().release_original(source['id'])
    assert knowledge.portability().release_original(source['id'],True)['released_bytes']==0
    knowledge.delete_source(source['id'])
    assert original.exists()


def test_empty_or_processing_sources_cannot_release_originals(knowledge):
    source = knowledge.import_source(filename='empty.txt',content_base64='')
    with pytest.raises(ValueError,match='finish'): knowledge.portability().release_original(source['id'])
    knowledge.process_source(source['id'])
    with pytest.raises(ValueError,match='No searchable'): knowledge.portability().release_original(source['id'],True)
    assert Path(source['path']).exists()


def test_correcting_evidence_requires_reviewing_training_example_again(knowledge):
    source=lesson(knowledge); chunk=source['chunks'][0]; archive=knowledge.portability()
    saved=archive.save_example(chunk['id'],'Which behavior is correct?','Check eligibility on the server.',True,True)
    knowledge.correct(chunk['id'],'Corrected evidence: the coupon rule applies to product categories.')
    assert not knowledge.store.get('learning_example',saved['id'])['reviewed']
    with zipfile.ZipFile(archive.export_bundle(False)['path']) as z:
        assert z.read('train.jsonl')==b''


def test_large_local_course_is_streamed_without_a_second_copy(knowledge,tmp_path):
    assert MAX_FILE_BYTES>=3*1024**3
    path = tmp_path/'large-course.mp4'
    with path.open('wb') as f: f.truncate(3*1024**3)
    source = knowledge.import_source(path=str(path),copy_original=False)
    assert source['source_bytes']==3*1024**3
    assert source['source_storage']=='linked' and not list((knowledge.store.root/'sources').iterdir())
    # This verifies large-file intake; a sparse fixture is not a valid lecture.

