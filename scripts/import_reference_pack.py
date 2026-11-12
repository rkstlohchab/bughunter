"""Import the bundled reference pack into a closed app's library, without network sources.

Run with backend/.venv/bin/python scripts/import_reference_pack.py [--data-dir DIR].
The desktop-owner lock prevents concurrent imports into an open application.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from bughunter.file_lock import try_lock
from bughunter.knowledge import KnowledgeService
from bughunter.reference_pack import SEED_KEY, import_pack
from bughunter.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    store = Store(args.data_dir)
    with (store.root / 'desktop.lock').open('a+') as owner:
        if not try_lock(owner):
            raise SystemExit('The app is open. The updated backend imports this pack on its next startup.')
        knowledge = KnowledgeService(store, start_worker=False)
        try:
            result = import_pack(knowledge)
            sources = []
            for source in result['sources']:
                if source['status'] not in {'ready', 'partial'}:
                    source = knowledge.process_source(source['id'])
                if source['status'] not in {'ready', 'partial'} or not source['chunk_count']:
                    raise RuntimeError(f"Source indexing failed: {source['filename']}: {source.get('error')}")
                sources.append({'id': source['id'], 'filename': source['filename'], 'chunks': source['chunk_count']})
            store.set_setting(SEED_KEY, {'source_ids': result['source_ids'], 'version': result['version']})
            print(json.dumps({'pack_id': result['pack_id'], 'data_dir': str(store.root), 'sources': sources}, indent=2))
        finally:
            knowledge.shutdown()


if __name__ == '__main__':
    main()
