"""Snapshots, checksums, and safe restore; no external services."""
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from bughunter.backup import create, verify, restore
from bughunter.store import Store


def test_snapshot_restores_sqlite_and_rewrites_local_paths(tmp_path):
    data = tmp_path / 'live'
    store = Store(data)
    file = data / 'sources' / 'lesson.md'
    file.write_text('Verified local lesson', encoding='utf8')
    store.put('source', {'title':'Lesson', 'path':str(file), 'status':'ready'}, id='source_1')
    store.put('project', {'name':'A'}, id='project_a')
    archive = tmp_path / 'private.zip'
    result = create(data, archive)
    assert result['files'] == 2
    assert verify(archive)['verified']
    assert archive.stat().st_mode & 0o077 == 0
    restored = tmp_path / 'restored'
    assert restore(archive, restored)['verified']
    new_store = Store(restored)
    assert new_store.get('project','project_a')['name'] == 'A'
    new_path = Path(new_store.get('source','source_1')['path'])
    assert new_path == restored / 'sources' / 'lesson.md'
    assert new_path.read_text() == 'Verified local lesson'
    with pytest.raises(FileExistsError):
        restore(archive, restored)


def test_reject_corrupt_archive_and_unsafe_path(tmp_path):
    data = tmp_path/'data'; Store(data)
    archive = tmp_path/'backup.zip'; create(data, archive)
    with zipfile.ZipFile(archive) as src:
        members = [(name,src.read(name)) for name in src.namelist()]
    with zipfile.ZipFile(tmp_path/'changed.zip','w') as dst:
        for name,contents in members:
            dst.writestr(name,b'changed' if name=='workbench.sqlite3' else contents)
    with pytest.raises(ValueError,match='mismatch'):
        verify(tmp_path/'changed.zip')
    with zipfile.ZipFile(tmp_path/'unsafe.zip','w') as dst:
        dst.writestr('../outside.txt','pwned')
        dst.writestr('manifest.json',json.dumps({'schema':1,'files':{}}))
    with pytest.raises(ValueError,match='unsafe'):
        restore(tmp_path/'unsafe.zip',tmp_path/'out')
    assert not (tmp_path/'outside.txt').exists()


def test_backup_with_active_wal_transaction_commits(tmp_path):
    data=tmp_path/'data';store=Store(data)
    store.put('work_task',{'kind':'memory_session','status':'running'},id='task1')
    create(data,tmp_path/'wal.zip')
    restore(tmp_path/'wal.zip',tmp_path/'restore')
    assert Store(tmp_path/'restore').get('work_task','task1')['status']=='running'
