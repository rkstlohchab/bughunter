import base64
import json
from pathlib import Path

import pytest

from bughunter import backup
from bughunter.app import Application
from bughunter.knowledge import KnowledgeService
from bughunter.retired_prompts import retire_prompt_workflow
from bughunter.store import Store


def test_retirement_archives_exact_sources_and_keeps_unrelated_notes(tmp_path):
    store = Store(tmp_path / 'live')
    knowledge = KnowledgeService(store, start_worker=False)
    def source(text, name):
        row = knowledge.import_source(filename=name, content_base64=base64.b64encode(text.encode()).decode())
        return knowledge.process_source(row['id'])
    old = source('Retired library example with uniquearchivekeyword.', 'bundled.md')
    hashes = json.loads((Path(__file__).parents[1] / 'bughunter/assets/retired_prompt_hashes.json').read_text())
    store.put('source', {**old, 'content_hash': next(iter(hashes))}, id=old['id'])
    keep = source('Keep the website coupon evidence.', 'ai-redteam-methods.md')
    trial = store.put('redteam_trial', {'published_source_id': None})
    ws = store.put('training_workspace', {'domain':'ai_redteam'})
    example = store.put('training_example', {'domain':'ai_redteam','workspace_id':ws['id']})
    ce = store.put('training_workspace', {'domain':'cheat_engine'})
    knowledge.shutdown()
    result = retire_prompt_workflow(store)
    assert result['sources_removed'] == 1 and result['records_removed'] == 3
    assert store.get('source', keep['id'])
    assert not store.get('source', old['id'])
    assert store.get('training_workspace', ce['id'])
    assert not store.get('training_example', example['id'])
    with store.connection() as db:
        assert not db.execute('SELECT 1 FROM knowledge_fts WHERE source_id=?', (old['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM knowledge_chunks WHERE source_id=?', (old['id'],)).fetchone()
    assert retire_prompt_workflow(store) == result
    archive = result['backup']['path']
    assert backup.verify(archive)['verified']
    backup.restore(archive, tmp_path / 'restored')
    restored = Store(tmp_path / 'restored')
    assert restored.get('redteam_trial', trial['id'])
    restored_source = restored.get('source', old['id'])
    assert 'uniquearchivekeyword' in Path(restored_source['path']).read_text()


def test_removed_rpc_and_legacy_domains_cannot_run_or_feed_chat(tmp_path):
    app = Application(tmp_path / 'app', primary=False)
    try:
        for method in ('redteam.cases','redteam.run','corpus.list','training.import_redteam','autopilot.export_training'):
            with pytest.raises(ValueError): app.dispatch(method, {})
        with pytest.raises(ValueError): app.training.create_workspace('ai_redteam','Legacy')
        ws = app.store.put('training_workspace', {'domain':'ai_redteam'})
        row = app.store.put('training_example', {'domain':'ai_redteam','workspace_id':ws['id'],
                            'status':'reviewed','instruction':'uniqueretiredkeyword', 'response':'retired'})
        assert app.training.list_workspaces() == []
        with pytest.raises(ValueError): app.training.export(ws['id'])
        with pytest.raises(ValueError): app.training.review(row['id'], True)
        assert app.chat._specialist_notes('uniqueretiredkeyword') == ''
        assert app.autopilot.plan(message='Test prompt injection on my AI')['workflow'] == 'chat'
        assert app.training.create_workspace('cheat_engine', 'Keep memory tools')
    finally:
        app.close()
