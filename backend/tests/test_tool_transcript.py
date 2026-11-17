"""Text-protocol providers must not echo or fabricate host tool turns."""
import json

import pytest

from bughunter import provider_api
from bughunter.provider_api import _routed_tool_turn, strip_fabricated_turns

SPECS = [{'name': 'game_session', 'description': 'attach', 'input_schema': {'type': 'object'}},
         {'name': 'library_search', 'description': 'search', 'input_schema': {'type': 'object'}}]

LEAKED = ('Requested tools: [{"id": "tc_1", "type": "function", "function": {"name": "game_session", '
          '"arguments": "{\\"target_id\\": \\"t1\\", \\"pid\\": 79126}"}}]\n\n'
          'USER: UNTRUSTED TOOL RESULT game_session: {"attached": true, "pid": 79126}')


@pytest.fixture
def reply(monkeypatch):
    """Route the 'gemini_cli' branch to a canned reply and capture the prompt."""
    captured = {}

    def complete(messages, system, model, effort, cancel_check=None):
        captured['messages'], captured['system'] = messages, system
        return captured['reply']
    import bughunter.gemini_subscription as gem
    monkeypatch.setattr(gem, 'complete', complete)
    return captured


def run(captured, text, transcript=None):
    captured['reply'] = text
    return _routed_tool_turn({'provider': 'gemini_cli', 'model': 'm'},
                             transcript or [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'go'}],
                             SPECS, 512)


def test_legacy_requested_tools_reply_becomes_a_real_call_and_drops_fake_result(reply):
    out = run(reply, LEAKED)
    assert out['tool_calls'] and out['tool_calls'][0]['name'] == 'game_session'
    assert out['tool_calls'][0]['input'] == {'target_id': 't1', 'pid': 79126}
    assert 'attached' not in json.dumps(out['message'])


def test_protocol_call_followed_by_fabricated_result_keeps_only_the_call(reply):
    out = run(reply, '{"tool": "library_search", "arguments": {"query": "hp"}}\nUSER: UNTRUSTED TOOL RESULT library_search: {"passages": []}')
    assert out['tool_calls'][0]['name'] == 'library_search' and out['tool_calls'][0]['input'] == {'query': 'hp'}


def test_final_answer_with_fabricated_tail_is_cut_and_labelled(reply):
    out = run(reply, 'The process is running.\nUSER: UNTRUSTED TOOL RESULT game_session: {"attached": true}')
    text = out['message']['content']
    assert out['tool_calls'] == [] and text.startswith('The process is running.')
    assert 'attached' not in text and 'nothing was executed' in text


def test_ordinary_answers_are_untouched(reply):
    body = 'Findings:\nUser: admin was observed in the login form.\nTool: Burp'
    out = run(reply, body)
    assert out['message']['content'] == body


def test_prior_calls_are_replayed_in_the_protocol_format(reply):
    transcript = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'go'},
                  {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'tc_1', 'type': 'function', 'function': {'name': 'library_search', 'arguments': '{"query": "hp"}'}}]},
                  {'role': 'tool', 'name': 'library_search', 'tool_call_id': 'tc_1', 'content': '{"passages": []}'}]
    run(reply, 'done', transcript)
    flat = '\n'.join(m['content'] for m in reply['messages'])
    assert 'Requested tools' not in flat and 'UNTRUSTED TOOL RESULT' not in flat
    assert '{"tool": "library_search", "arguments": {"query": "hp"}}' in flat
    assert '[host tool result: library_search' in flat
    assert 'Never write a tool result' in reply['system']


def test_unknown_tool_name_still_rejected(reply):
    with pytest.raises(ValueError, match='invalid tool call'):
        run(reply, '{"tool": "shell_root", "arguments": {}}')


def test_strip_helper_for_saved_transcripts():
    cleaned, removed = strip_fabricated_turns(LEAKED)
    assert removed and cleaned == ''
    assert strip_fabricated_turns('plain answer') == ('plain answer', False)
