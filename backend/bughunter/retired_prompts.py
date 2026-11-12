"""Retire the former prompt-testing workspace without discarding its history.

Only exact bundled content hashes and explicit workflow provenance are selected.
An offline, verified backup precedes removal from active retrieval and exports.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import backup
from .knowledge import KnowledgeService
from .store import new_id, now


def retire_prompt_workflow(store):
    marker = 'prompt_workflow_retired_v1'
    if store.setting(marker, None):
        return store.setting(marker)
    hashes = json.loads((Path(__file__).parent / 'assets' / 'retired_prompt_hashes.json').read_text())
    trials = store.list('redteam_trial')
    published = {row.get('published_source_id') for row in trials}
    sources = [row for row in store.list('source')
               if row.get('content_hash') in hashes or row['id'] in published]
    records = [(kind, row['id']) for kind in ('redteam_case', 'redteam_trial', 'prompt_corpus_review')
               for row in store.list(kind)]
    workspaces = {row['id'] for row in store.list('training_workspace') if row.get('domain') == 'ai_redteam'}
    records += [('training_workspace', id) for id in workspaces]
    records += [('training_example', row['id']) for row in store.list('training_example')
                if row.get('domain') == 'ai_redteam' or row.get('workspace_id') in workspaces]
    result = {'completed_at': now(), 'sources_removed': len(sources), 'records_removed': len(records), 'backup': None}
    if sources or records:
        destination = store.root.parent / (store.root.name + '-archives') / (new_id('retired-prompts') + '.zip')
        result['backup'] = backup.create(store.root, destination)
        # No ingestion workers run during migration. Use the existing deletion
        # path so chunks, vectors, graph nodes and learning examples agree.
        knowledge = KnowledgeService(store, start_worker=False)
        try:
            for source in sources:
                knowledge.delete_source(source['id'])
        finally:
            knowledge.shutdown()
        with store.connection(immediate=True) as db:
            db.executemany('DELETE FROM entities WHERE kind=? AND id=?', records)
    store.set_setting(marker, result)
    return result
