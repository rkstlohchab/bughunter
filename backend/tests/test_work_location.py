"""Foreground/background follows the user's words instead of a settings toggle."""
import time

import pytest

from bughunter.app import Application
from bughunter.harness_modes import work_location


@pytest.mark.parametrize('text,expected', [
    ('Open Calculator and show me while you do it', False),
    ('I want to watch the AI move the cursor', False),
    ('do it in front of me so I can record the screen', False),
    ('Clean up downloads in the background', True),
    ('quietly rename these files, don\'t use my mouse', True),
    ('show me nothing, run it in the background', True),
    ('Summarise this PDF', None),
    ('User: admin appears on screen 3 of the report', None),
    ('do it on my screen', False),
])
def test_phrases(text, expected):
    assert work_location(text) is expected


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path, primary=False)
    monkeypatch.setattr('bughunter.provider_api.single_tool_turn',
                        lambda **k: {'message': {'role': 'assistant', 'content': 'ok'}, 'tool_calls': []})
    yield a
    a.close()


def settle(app, hid):
    deadline = time.monotonic() + 3
    while app.harness.get(hid)['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(.02)


def test_start_infers_and_follow_up_switches(app):
    task = app.harness.start('Open Notes and let me watch', ['app'], settings_override={'provider': 'ollama', 'model': 'm'})
    assert app.harness.get(task['id'])['background'] is False
    settle(app, task['id'])
    app.harness.send(task['id'], 'continue, but do the rest in the background')
    assert app.store.get('harness_session', task['id'])['background'] is True
    settle(app, task['id'])
    app.harness.send(task['id'], 'next item please')
    assert app.store.get('harness_session', task['id'])['background'] is True


def test_default_is_background_and_explicit_flag_still_honoured(app):
    plain = app.harness.start('Summarise my library', ['app'], settings_override={'provider': 'ollama', 'model': 'm'})
    assert app.harness.get(plain['id'])['background'] is True
    settle(app, plain['id'])
    forced = app.harness.start('Summarise my library', ['app'], background=False, settings_override={'provider': 'ollama', 'model': 'm'})
    assert app.harness.get(forced['id'])['background'] is False
