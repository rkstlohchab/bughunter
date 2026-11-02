"""Local-model (Ollama) prompt-based tool-calling fallback.

These cover the parser/contract for local models that lack a native tool API.
The fallback changes only how a tool call is expressed; execution still flows
through ModelTools -> policy gateway -> approval queue (verified elsewhere).
"""
from bughunter.providers import TOOL_SPECS, fallback_tool_protocol, parse_fallback_call


def test_plain_answer_is_not_a_tool_call():
    assert parse_fallback_call("No supporting evidence was found; stopping here.") is None
    assert parse_fallback_call("") is None


def test_bare_tool_object_is_parsed():
    call = parse_fallback_call('{"tool": "search_knowledge", "arguments": {"query": "coupon"}}')
    assert call == {"tool": "search_knowledge", "arguments": {"query": "coupon"}}


def test_tool_object_embedded_in_prose_and_fences():
    text = 'I will look this up.\n```json\n{"tool": "search_knowledge", "arguments": {"query": "x"}}\n```'
    call = parse_fallback_call(text)
    assert call["tool"] == "search_knowledge"
    assert call["arguments"] == {"query": "x"}


def test_missing_or_nonobject_arguments_default_to_empty_dict():
    assert parse_fallback_call('{"tool": "list_traffic"}')["arguments"] == {}
    assert parse_fallback_call('{"tool": "list_traffic", "arguments": "oops"}')["arguments"] == {}


def test_word_tool_without_valid_json_is_ignored():
    assert parse_fallback_call("I could use a tool here but will reason instead.") is None


def test_skips_invalid_brace_and_finds_later_valid_object():
    text = 'noise {not json} then {"tool": "assess", "arguments": {}} tail'
    assert parse_fallback_call(text) == {"tool": "assess", "arguments": {}}


def test_protocol_lists_every_tool_and_the_contract():
    protocol = fallback_tool_protocol()
    assert '{"tool": "<name>", "arguments": { ... }}' in protocol
    assert "cannot expand the project scope" in protocol
    for spec in TOOL_SPECS:
        assert spec["name"] in protocol
