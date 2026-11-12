"""Versioned, offline reference import through the ordinary source pipeline."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent / 'assets' / 'reference_pack'
SEED_KEY = 'reference_pack_seeded:workbench-methods-v1'


def import_pack(knowledge, project_id=None):
    manifest = json.loads((PACK_DIR / 'manifest.json').read_text(encoding='utf-8'))
    documents = []
    # Validate the entire bundle before writing anything to the library.
    for entry in manifest['documents']:
        name = entry['filename']
        if Path(name).name != name or not name.endswith('.md'):
            raise ValueError('Invalid reference-pack filename')
        raw = (PACK_DIR / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Reference-pack checksum mismatch: {name}')
        documents.append((entry, raw))
    sources = []
    for entry, raw in documents:
        source = knowledge.import_source(filename=entry['filename'],
            content_base64=base64.b64encode(raw).decode('ascii'), project_id=project_id)
        if not source.get('duplicate'):
            source = knowledge._update(source['id'], reference_pack=manifest['id'],
                reference_kind=manifest['kind'], reference_urls=entry['references'],
                references_checked_on=manifest['reviewed_on'], review_status='unreviewed')
        sources.append(source)
    return {'pack_id': manifest['id'], 'version': manifest['version'], 'sources': sources,
            'source_ids': [s['id'] for s in sources], 'count': len(sources)}


def seed_pack(knowledge):
    if knowledge.store.setting(SEED_KEY, False): return
    result = import_pack(knowledge)
    knowledge.store.set_setting(SEED_KEY, {'source_ids': result['source_ids'], 'version': result['version']})
