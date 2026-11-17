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
    assert first['count'] == 17
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
    assert len(knowledge.store.list('source')) == 17


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
    assert len(knowledge.store.list('source')) == 16
    import_pack(knowledge)
    assert len(knowledge.store.list('source')) == 17


def test_upgrade_adds_only_new_documents_and_keeps_deletions(knowledge):
    from bughunter.reference_pack import PREVIOUS_SEEDS
    v1_key, v1_names = next(iter(PREVIOUS_SEEDS.items()))
    first = import_pack(knowledge)
    removed = next(s['id'] for s in first['sources'] if s['filename'] == 'food-ordering.md')
    knowledge.delete_source(removed)
    knowledge.store.set_setting(v1_key, {'version': 1})
    for source in knowledge.store.list('source'):
        if source['filename'] not in v1_names: knowledge.delete_source(source['id'])
    seed_pack(knowledge)
    names = {s['filename'] for s in knowledge.store.list('source')}
    assert 'food-ordering.md' not in names
    assert {'macos-memory-research.md', 'macos-practice-targets.md', 'bughunter-app-guide.md', 'website-testing-playbook.md'} <= names


def test_corrupted_bundle_is_rejected_before_import(knowledge, tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    shutil.copytree(PACK_DIR, bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    (bundle / manifest['documents'][-1]['filename']).write_text('Changed after manifest was written')
    monkeypatch.setattr('bughunter.reference_pack.PACK_DIR', bundle)
    with pytest.raises(ValueError, match='checksum mismatch'): import_pack(knowledge)
    assert not knowledge.store.list('source')

def test_manifest_covers_every_bundled_document_and_matches_content():
    import hashlib
    manifest = json.loads((PACK_DIR / 'manifest.json').read_text())
    assert len(manifest['documents']) == 17
    assert {entry['filename'] for entry in manifest['documents']} == {path.name for path in PACK_DIR.glob('*.md')}
    for entry in manifest['documents']:
        raw = (PACK_DIR / entry['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['sha256'], entry['filename']
        text = raw.decode('utf-8')
        assert text.splitlines()[0] == '# ' + entry['title']
        assert all(url in text for url in entry['references'])


def test_fresh_primary_application_seeds_reference_pack(tmp_path):
    from bughunter.app import Application
    from bughunter.reference_pack import SEED_KEY
    app = Application(tmp_path / 'fresh-primary', primary=True)
    try:
        seeded = app.store.setting(SEED_KEY)
        assert seeded['version'] == 3
        assert len(seeded['source_ids']) == 17
        assert all(app.store.get('source', source_id)['reference_pack'] == 'workbench-methods-v3'
                   for source_id in seeded['source_ids'])
    finally:
        app.close()


def test_seed_waits_while_stopped_and_never_breaks_startup(tmp_path, monkeypatch):
    from bughunter.app import Application
    from bughunter.reference_pack import SEED_KEY
    from bughunter.store import Store
    store = Store(tmp_path / 'stopped')
    store.set_setting('emergency_stop', True)
    app = Application(tmp_path / 'stopped', primary=True)
    try:
        assert app.store.setting(SEED_KEY) is None
    finally:
        app.close()
    monkeypatch.setattr('bughunter.reference_pack.import_pack', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    app = Application(tmp_path / 'broken', primary=True)
    try:
        assert any(e['type'] == 'reference_pack.seed_failed' for e in app.store.events(limit=500))
    finally:
        app.close()
