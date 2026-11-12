"""Real loopback commerce regression plus offline cited-reference retrieval.

Run with backend/.venv/bin/python scripts/smoke_commerce_knowledge.py --output DIR.
DIR must be new: it retains the database and correlated evidence for inspection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from bughunter.app import Application
from bughunter.commerce_audit import CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        output = Path(tempfile.mkdtemp(prefix='bughunter-commerce-knowledge-'))
    app = Application(output / 'data', primary=False)
    report = {'target': 'application-owned loopback fixture', 'external_sites_tested': False}
    task = None
    try:
        pack = app.dispatch('knowledge.reference_pack.import')
        sources = [app.knowledge.process_source(s) for s in pack['source_ids']]
        assert all(s['status'] == 'ready' and s['chunk_count'] for s in sources), sources
        queries = {}
        for query, filename in [('FOOD10', 'food-ordering.md'), ('GraphQL', 'graphql-classification.md'),
                                 ('ReadProcessMemory', 'native-memory-lifecycle.md'),
                                 ('CVE-2023-7028', 'races-and-case-studies.md')]:
            expected = next(s['id'] for s in sources if s['filename'] == filename)
            hits = app.dispatch('knowledge.search', {'query': query, 'limit': 30, 'project_id': None})
            assert any(hit['source_id'] == expected for hit in hits), query
            queries[query] = {'expected_source': expected, 'found': True}
        assert app.dispatch('knowledge.reference_pack.import')['source_ids'] == pack['source_ids']
        report['knowledge'] = {'pack_id': pack['pack_id'], 'sources': len(sources),
            'chunks': sum(s['chunk_count'] for s in sources), 'retrieval': queries, 'deduplication': 'passed'}
        task = app.dispatch('commerce.lab.run', {'approved': True})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            task = app.store.get('work_task', task['id'])
            if task['status'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        report['commerce'] = task
        actions = app.store.list('action', project_id=task['project_id'])
        report['actions'] = actions
        report['receipts'] = [app.store.get('traffic', eid) for action in actions for eid in action['evidence_ids']]
        assert task['status'] == 'completed', task
        assert len(task['result']['cases']) == len(CASES) * 2
        assert task['result']['passed_controls'] == task['result']['observed_failures'] == len(CASES)
        assert all(case['passed'] for case in task['result']['cases'])
        assert len(report['receipts']) == len(actions) == len(CASES) * 2
        assert all(a['request']['url'].startswith('http://127.0.0.1:') for a in actions)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if task and task['id'] in app.commerce._stop:
            app.commerce.cancel(task['id'])
            for _ in range(100):
                if task['id'] not in app.commerce._stop: break
                time.sleep(.05)
        app.lab.stop()
        app.close()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(output / 'report.json'),
        'sources': report['knowledge']['sources'], 'chunks': report['knowledge']['chunks'],
        'http_cases': len(task['result']['cases']), 'fixed_controls': task['result']['passed_controls']}))


if __name__ == '__main__':
    main()
