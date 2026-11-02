"""ZIP prompt research is classified only after bounded, non-executing import."""
from __future__ import annotations
import base64
import io
import zipfile

import pytest
from bughunter.app import Application


def makezip(items):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, value in items.items():
            archive.writestr(name, value)
    return base64.b64encode(buffer.getvalue()).decode()


def test_archive_classifies_cited_passages_without_executing_prompts(tmp_path):
    app = Application(tmp_path / 'research', primary=False)
    try:
        resource = app.dispatch('sources.import', {'filename':'prompt_collection.zip', 'content_base64':makezip({
            'examples/hierarchy.md': '# Case A\n\nTest for system prompt authority spoofing and override attempts.',
            'examples/encoded.txt': 'This case compares Base64 and Unicode encoded prompt variants.',
        })})
        app.knowledge.process_source(resource['id'])
        report = app.dispatch('redteam.archive.inspect', {'source_id':resource['id']})
        names = [r['name'] for r in report['categories']]
        assert 'Authority and instruction spoofing' in names
        assert 'Encoding and obfuscation' in names
        assert report['ready']
        assert any('hierarchy.md' in example['locator'] for example in report['examples'])
        assert not app.dispatch('redteam.cases')  # no auto-promotion or inferred test outcomes
    finally:
        app.shutdown()


def test_unsafe_zip_paths_are_rejected_by_library(tmp_path):
    app = Application(tmp_path / 'research', primary=False)
    try:
        resource = app.dispatch('sources.import', {'filename':'bad.zip', 'content_base64':makezip({'../escape.txt':'test'})})
        failed = app.knowledge.process_source(resource['id'])
        assert failed['status'] == 'error'
        assert 'unsafe member path' in failed['error']
    finally:
        app.shutdown()


def test_archive_inspection_not_available_for_nonzip(tmp_path):
    app = Application(tmp_path / 'research', primary=False)
    try:
        source = app.dispatch('sources.import', {'filename':'note.txt','content_base64':base64.b64encode(b'jailbreak test').decode()})
        app.knowledge.process_source(source['id'])
        with pytest.raises(ValueError,match='ZIP archive'):
            app.dispatch('redteam.archive.inspect',{'source_id':source['id']})
    finally:
        app.shutdown()
