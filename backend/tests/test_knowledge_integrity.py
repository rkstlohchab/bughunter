"""Retrieval remains scoped and derived knowledge follows its current evidence."""
import base64
import json
import struct
import zipfile

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path):
    service = KnowledgeService(Store(tmp_path / 'data'), start_worker=False)
    for id in ('project_a', 'project_b'):
        service.store.put('project', {'name': id}, id=id)
    yield service
    service.shutdown()


def source(service, text, project_id=None):
    value = service.import_source(filename='notes.md', project_id=project_id,
                                  content_base64=base64.b64encode(text.encode()).decode())
    service.process_source(value['id'])
    return service.get_source(value['id'])


def crowded_project(service):
    other = source(service, 'Other project', 'project_b')
    service._index(other, [{'text': 'quartz', 'locator': f'Page {n}'} for n in range(180)])
    service._update(other['id'], chunk_count=180)
    return service.get_source(other['id'])


def test_scope_is_applied_before_keyword_candidate_limit(knowledge):
    private = crowded_project(knowledge)
    allowed = source(knowledge, 'quartz ' + 'context ' * 100, 'project_a')
    shared = source(knowledge, 'quartz ' + 'shared explanation ' * 100)
    hits = knowledge.search_for_project('quartz', 'project_a', limit=2)
    assert {r['source_id'] for r in hits} == {allowed['id'], shared['id']}
    assert private['id'] not in {r['source_id'] for r in hits}
    assert {r['source_id'] for r in knowledge.search_for_project('quartz', None)} == {shared['id']}


def test_scope_is_applied_before_semantic_candidate_limit(knowledge, monkeypatch):
    private = crowded_project(knowledge)
    allowed = source(knowledge, 'Allowed passage', 'project_a')
    with knowledge.store.connection(immediate=True) as db:
        for record in private['chunks'] + allowed['chunks']:
            vector = [0., 1.] if record['source_id'] == private['id'] else [1., 0.]
            db.execute('INSERT INTO knowledge_vectors VALUES(?,?,?,?)',
                       (record['id'], 'test@digest', 2, struct.pack('<2f', *vector)))
    knowledge._vec_available = True
    knowledge.store.set_setting('settings', {'use_local_embeddings': True})
    monkeypatch.setattr(knowledge, '_embed', lambda texts: ('test@digest', [[0., 1.]]))
    def local_distance(db):
        db.create_function('vec_distance_cosine', 2,
                           lambda a, b: sum((x-y)**2 for x, y in zip(struct.unpack('<2f', a), struct.unpack('<2f', b))))
        return True
    monkeypatch.setattr(knowledge, '_load_vec', local_distance)
    hits = knowledge.search_for_project('no lexical overlap', 'project_a', limit=1)
    assert [r['source_id'] for r in hits] == [allowed['id']]


def test_frame_replacement_invalidates_old_graph_and_reviewed_examples(knowledge):
    record = source(knowledge, 'Plain video notes')
    frame = knowledge.store.root / 'artifacts' / record['id'] / 'frame.jpg'
    frame.write_bytes(b'local frame fixture')
    knowledge._update(record['id'], frames=[{'path': str(frame), 'time': 0, 'locator': 'Frame 0'}])
    first = knowledge.interpret_frame(record['id'], 'frame.jpg', 'Coupon eligibility appears in the frame.')
    knowledge.enrich(record['id'], [{'chunk_id': first['id'], 'quote': 'Coupon eligibility',
                                    'text': 'Check coupon eligibility'}])
    example = knowledge.portability().save_example(first['id'], 'What appears?', 'A coupon.', True, True)
    knowledge.interpret_frame(record['id'], 'frame.jpg', 'The frame shows a neutral diagram.')
    graph = knowledge.graph()
    assert all(n['label'] not in {'Check coupon eligibility', 'Coupon eligibility testing'} for n in graph['nodes'])
    assert not knowledge.store.get('learning_example', example['id'])['reviewed']
    assert knowledge.search('coupon') == []
    assert knowledge.integrity()['status'] == 'healthy'


def test_reindex_replaces_timestamp_edges_and_deduplicates_passages(knowledge):
    record = source(knowledge, 'Temporal notes')
    segments = [{'text': text, 'locator': text, 'start': start}
                for text, start in [('First frame', 0), ('Second frame', 1), ('Third frame', 2)]]
    knowledge._index(record, segments)
    segments[0]['start'] = 3
    assert knowledge._index(record, segments + [segments[0]]) == 3
    knowledge._update(record['id'], chunk_count=3)
    current = knowledge.get_source(record['id'])
    ids = {c['text']: c['id'] for c in current['chunks']}
    edges = {(e['source'], e['target']) for e in knowledge.graph()['edges'] if e['relation'] == 'precedes'}
    assert edges == {(ids['Second frame'], ids['Third frame']), (ids['Third frame'], ids['First frame'])}
    assert knowledge.integrity()['status'] == 'healthy'


def test_integrity_checks_one_scope_without_repairing_or_exposing_sources(knowledge):
    a = source(knowledge, 'Shared-looking private alpha material', 'project_a')
    b = source(knowledge, 'Private beta material', 'project_b')
    shared = source(knowledge, 'Common material')
    with knowledge.store.connection(immediate=True) as db:
        db.execute('DELETE FROM knowledge_fts WHERE source_id=?', (b['id'],))
        db.execute('INSERT INTO knowledge_vectors VALUES(?,?,?,?)', ('missing_chunk', 'test', 2, b'12345678'))
    report = knowledge.dispatch('knowledge.integrity', {'project_id': 'project_a'})
    assert report['status'] == 'healthy' and report['read_only']
    assert report['counts']['sources'] == 2 and report['counts']['passages'] == 2
    assert all(r['id'] not in json.dumps(report) for r in (a, b, shared))
    global_report = knowledge.integrity()
    assert global_report['status'] == 'needs_attention'
    assert {i['code'] for i in global_report['issues']} == {'missing_keyword_entries', 'orphan_vectors'}
    assert knowledge.integrity(None)['counts']['sources'] == 1
    assert knowledge.integrity()['issues'] == global_report['issues']  # Diagnostic never repairs silently.


def test_equal_questions_in_different_projects_preserve_both_examples(knowledge):
    a = source(knowledge, 'Alpha evidence', 'project_a')
    b = source(knowledge, 'Beta evidence', 'project_b')
    archive = knowledge.portability()
    first = archive.save_example(a['chunks'][0]['id'], 'What does the source say?', 'Alpha.', True, True)
    second = archive.save_example(b['chunks'][0]['id'], 'What does the source say?', 'Beta.', True, True)
    assert first['id'] != second['id']
    assert knowledge.store.get('learning_example', first['id'])['answer'] == 'Alpha.'
    assert knowledge.store.get('learning_example', second['id'])['answer'] == 'Beta.'
    with pytest.raises(ValueError, match='another split'):
        archive.save_example(b['chunks'][0]['id'], 'What does the source say?', 'Beta.', True, True, 'evaluation')


def test_training_export_rechecks_evidence_and_preserves_stale_records(knowledge):
    record = source(knowledge, 'An exact source passage.')
    archive = knowledge.portability()
    example = archive.save_example(record['chunks'][0]['id'], 'What is stated?', 'An exact passage.', True, True)
    # Simulate legacy stale review flags or a caller that rewrote a chunk directly.
    with knowledge.store.connection(immediate=True) as db:
        db.execute('UPDATE knowledge_chunks SET text=? WHERE id=?', ('Changed source passage.', record['chunks'][0]['id']))
    assert 'stale_reviewed_examples' in {i['code'] for i in knowledge.integrity()['issues']}
    with zipfile.ZipFile(archive.export_bundle(False)['path']) as bundle:
        assert bundle.read('train.jsonl') == b''
        assert example['id'] in bundle.read('examples.jsonl').decode()


def test_redacted_support_remains_eligible_for_training_export(knowledge):
    record = source(knowledge, 'Authorization: Bearer fixture-secret\nA passage about server validation.')
    archive = knowledge.portability()
    example = archive.save_example(record['chunks'][0]['id'], 'What is checked?', 'Server validation.', True, True)
    assert knowledge.integrity()['status'] == 'healthy'
    with zipfile.ZipFile(archive.export_bundle(False)['path']) as bundle:
        assert example['id'] in bundle.read('train.jsonl').decode()
        assert 'fixture-secret' not in bundle.read('examples.jsonl').decode()
