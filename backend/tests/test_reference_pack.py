import json
import shutil

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.reference_pack import PACK_DIR, import_pack, seed_pack
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path):
    service = KnowledgeService(Store(tmp_path), start_worker=False)
    yield service
    service.shutdown()


def test_reference_pack_import_search_provenance_and_deduplication(knowledge):
    first = import_pack(knowledge)
    assert first['count'] == 10
    for source in first['sources']:
        indexed = knowledge.process_source(source['id'])
        assert indexed['status'] == 'ready', indexed
        assert indexed['reference_kind'] == 'authored_synthesis'
        assert indexed['review_status'] == 'unreviewed'
        assert indexed['reference_urls']
    for query, filename in [('FOOD10', 'food-ordering.md'), ('GraphQL', 'graphql-classification.md'),
                            ('CVE-2023-7028', 'races-and-case-studies.md'),
                            ('ReadProcessMemory', 'native-memory-lifecycle.md')]:
        expected = next(s['id'] for s in first['sources'] if s['filename'] == filename)
        assert any(hit['source_id'] == expected for hit in knowledge.search(query, limit=30, project_id=None))
    assert import_pack(knowledge)['source_ids'] == first['source_ids']
    assert len(knowledge.store.list('source')) == 10


def test_reference_pack_project_scope(knowledge):
    own = knowledge.store.put('project', {'name': 'Own'})
    foreign = knowledge.store.put('project', {'name': 'Other'})
    result = import_pack(knowledge, project_id=own['id'])
    for source in result['sources']: knowledge.process_source(source['id'])
    assert knowledge.search('FOOD10', project_id=own['id'])
    assert not knowledge.search('FOOD10', project_id=foreign['id'])


def test_seed_does_not_restore_deleted_reference(knowledge):
    seed_pack(knowledge)
    removed = knowledge.store.list('source')[0]['id']
    knowledge.delete_source(removed)
    seed_pack(knowledge)
    assert knowledge.store.get('source', removed) is None
    assert len(knowledge.store.list('source')) == 9
    import_pack(knowledge)
    assert len(knowledge.store.list('source')) == 10


def test_corrupted_bundle_is_rejected_before_import(knowledge, tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    shutil.copytree(PACK_DIR, bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    (bundle / manifest['documents'][-1]['filename']).write_text('Changed after manifest was written')
    monkeypatch.setattr('bughunter.reference_pack.PACK_DIR', bundle)
    with pytest.raises(ValueError, match='checksum mismatch'): import_pack(knowledge)
    assert not knowledge.store.list('source')
