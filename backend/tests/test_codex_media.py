"""Hermetic local-image app-server serialization and cancellation regressions."""
import io
import json
import os
from pathlib import Path
import signal
import threading
import time
from unittest.mock import Mock

import pytest

from bughunter.codex_subscription import CodexSession, _ACTIVE


def session_fixture(monkeypatch):
    session = CodexSession(timeout=1)
    calls, sent = [], []
    def call(method, params):
        calls.append((method, params))
        if method == 'account/read': return {'account': {'type': 'chatgpt'}}
        if method == 'config/read': return {'config': {'mcp_servers': {}}}
        assert method == 'thread/start'
        return {'thread': {'id': 'media-thread'}}
    monkeypatch.setattr(session, 'call', call)
    monkeypatch.setattr(session, 'send', sent.append)
    session.events.put({'method': 'item/completed', 'params': {'threadId': 'media-thread',
        'item': {'type': 'agentMessage', 'text': 'Visible frame evidence.'}}})
    session.events.put({'method': 'turn/completed', 'params': {'threadId': 'media-thread',
        'turn': {'status': 'completed'}}})
    return session, calls, sent


def test_ordered_local_images_and_medium_effort_match_installed_schema(tmp_path, monkeypatch):
    session, calls, sent = session_fixture(monkeypatch)
    images = [tmp_path / f'frame-{i}.jpg' for i in range(3)]
    for image in images: image.write_bytes(b'image fixture')
    assert session.run('requested-model', 'system', 'Narrator context and timestamps',
                       images=images, reasoning_effort='medium') == 'Visible frame evidence.'
    turn = sent[0]['params']
    assert turn['effort'] == 'medium'
    assert turn['input'] == [{'type': 'text', 'text': 'Narrator context and timestamps'}] + [
        {'type': 'localImage', 'path': str(image.resolve())} for image in images]
    thread = next(params for method, params in calls if method == 'thread/start')
    assert thread['dynamicTools'] == []
    assert thread['environments'] == [] and thread['selectedCapabilityRoots'] == []
    assert thread['allowProviderModelFallback'] is False


def test_existing_text_turn_serialization_is_unchanged(monkeypatch):
    session, _, sent = session_fixture(monkeypatch)
    session.run('model', 'system', 'text')
    assert sent[0]['params'] == {'threadId': 'media-thread', 'input': [{'type': 'text', 'text': 'text'}],
                                 'environments': []}


@pytest.mark.parametrize('kind', ['too-many', 'directory', 'symlink', 'oversize', 'empty'])
def test_invalid_media_is_rejected_before_account_or_thread_requests(tmp_path, monkeypatch, kind):
    session, calls, sent = session_fixture(monkeypatch)
    path = tmp_path / 'image.jpg'; path.write_bytes(b'image')
    if kind == 'too-many': images = [path] * 4
    elif kind == 'directory': images = [tmp_path]
    elif kind == 'symlink':
        alias = tmp_path / 'alias.jpg'; alias.symlink_to(path); images = [alias]
    else:
        with path.open('wb') as stream: stream.truncate(10 * 1024 * 1024 + 1 if kind == 'oversize' else 0)
        images = [path]
    with pytest.raises(ValueError): session.run('model', 's', 'p', images=images)
    assert not calls and not sent


def test_unsupported_effort_does_not_start_request(monkeypatch):
    session, calls, _ = session_fixture(monkeypatch)
    with pytest.raises(ValueError, match='medium'):
        session.run('model', 's', 'p', reasoning_effort='unknown')
    assert not calls


def test_callback_interrupts_wait_and_send_without_writing():
    def stopped(): raise InterruptedError('source cancelled')
    session = CodexSession(cancel_check=stopped)
    session.process = Mock(stdin=io.StringIO())
    with pytest.raises(InterruptedError, match='source cancelled'):
        session.send({'method': 'turn/start'})
    assert not session.process.stdin.getvalue()
    started = time.monotonic()
    with pytest.raises(InterruptedError): session.receive(started + 120)
    assert time.monotonic() - started < .5


def test_receive_checks_callback_while_waiting(monkeypatch):
    count = 0
    def check():
        nonlocal count
        count += 1
        if count == 2: raise InterruptedError('source cancelled while pending')
    session = CodexSession(cancel_check=check)
    started = time.monotonic()
    with pytest.raises(InterruptedError, match='while pending'):
        session.receive(started + 120)
    assert time.monotonic() - started < .5


def test_cancelled_context_terminates_child_cleans_workspace_and_reader(tmp_path, monkeypatch):
    pending = threading.Event()
    def check():
        if pending.is_set(): raise InterruptedError('operator stopped')
    process = Mock(pid=123456, stdin=io.StringIO(), stdout=io.StringIO(''), poll=Mock(return_value=None))
    monkeypatch.setattr('bughunter.codex_subscription.shutil.which', lambda name: '/fixture/codex')
    monkeypatch.setattr('bughunter.codex_subscription.subprocess.Popen', lambda *a, **kw: process)
    killed = []
    monkeypatch.setattr('bughunter.codex_subscription.os.killpg', lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(CodexSession, 'call', lambda *a: {})
    session = CodexSession(cancel_check=check)
    with pytest.raises(InterruptedError):
        with session:
            workspace = Path(session.workspace.name)
            assert workspace.exists()
            pending.set()
            session.receive(time.monotonic() + 60)
    assert killed == [(123456, signal.SIGTERM)]
    assert not workspace.exists()
    assert not session._reader.is_alive()
    assert session not in _ACTIVE
