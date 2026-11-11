"""Live context compaction: bound the IN-RAM working context sent to the provider on a
long run without ever breaking provider message structure.

The guarantees verified here (AGENTS.md: never break tool-call pairing or message
structure):

1. compact_context shrinks only the string CONTENT of OLD tool-RESULT messages, for each
   of the four provider shapes (anthropic blocks, openai function_call_output, chat
   role:"tool", and the fallback "Result of <tool>:" message). Message count, order,
   roles, tool_use/call_id pairing ids, images, the system/first framing message, and the
   most recent window are all left identical, and the serialized size drops.
2. Under budget, the list is returned unchanged.
3. Against the real RunManager._api loop (ollama path, httpx stubbed like
   test_autonomy_gate_and_resume.py), a run whose tool returns a huge string every turn
   stays bounded and still completes - nothing is dropped and the system/framing message
   is never touched.
"""
import json
import threading

import pytest

from bughunter.app import Application
from bughunter.model_tools import ModelTools
from bughunter.providers import (DEFAULT_SETTINGS, compact_context,
                                  CONTEXT_COMPACT_BUDGET_BYTES, COMPACT_KEEP_RECENT)
import bughunter.providers as providers


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


FRAMING = {"role": "user", "content": "Current project: p."}
BIG = "A" * 6000      # one OLD result, must be elided
KEPT = "B" * 6000     # a recent-window result, must survive untouched
ELIDED = "elided to fit context"


def _size(messages):
    return len(json.dumps(messages, default=str).encode())


def _assert_structure_preserved(out, src):
    assert len(out) == len(src)                      # no message dropped/merged/split
    assert out[0] == src[0]                          # system/first framing message untouched
    assert out[-1] == src[-1] and out[-2] == src[-2]  # recent window untouched
    assert _size(out) < _size(src)                   # serialized size actually dropped


# ---- per-shape unit guarantees (keep_recent=2 keeps the lists small) --------

def test_compact_anthropic_tool_result_blocks():
    old = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_old",
         "content": [{"type": "text", "text": BIG},
                     {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "IMGDATA"}}]}]}
    assistant = {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_new", "name": "probe_url", "input": {}}]}
    recent = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_new", "content": [{"type": "text", "text": KEPT}]}]}
    src = [FRAMING, old, assistant, recent]
    out = compact_context(src, "anthropic", budget=1000, keep_recent=2)
    _assert_structure_preserved(out, src)
    block = out[1]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "tu_old"  # pairing id intact
    assert ELIDED in block["content"][0]["text"] and BIG not in block["content"][0]["text"]
    assert block["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "IMGDATA"}}
    assert out[2] == assistant                        # tool_use request untouched (recent window)
    assert out[3]["content"][0]["content"][0]["text"] == KEPT


def test_compact_openai_function_call_output():
    old = {"type": "function_call_output", "call_id": "co_old", "output": BIG}
    assistant = {"type": "function_call", "call_id": "co_new", "name": "probe_url", "arguments": "{}"}
    recent = {"type": "function_call_output", "call_id": "co_new", "output": KEPT}
    src = [FRAMING, old, assistant, recent]
    out = compact_context(src, "openai", budget=1000, keep_recent=2)
    _assert_structure_preserved(out, src)
    assert out[1]["call_id"] == "co_old"              # pairing id intact
    assert ELIDED in out[1]["output"] and BIG not in out[1]["output"]
    assert out[2] == assistant                        # function_call request untouched
    assert out[3]["output"] == KEPT


def test_compact_chat_role_tool_content():
    old = {"role": "tool", "tool_call_id": "tc_old", "content": BIG}
    assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "tc_new", "function": {"name": "probe_url", "arguments": "{}"}}]}
    recent = {"role": "tool", "tool_name": "probe_url", "content": KEPT}   # ollama variant (tool_name)
    src = [FRAMING, old, assistant, recent]
    out = compact_context(src, "ollama", budget=1000, keep_recent=2)
    _assert_structure_preserved(out, src)
    assert out[1]["tool_call_id"] == "tc_old"         # pairing id intact
    assert ELIDED in out[1]["content"] and BIG not in out[1]["content"]
    assert out[2] == assistant                        # tool_calls request untouched
    assert out[3]["content"] == KEPT


def test_compact_fallback_result_of_message():
    trailer = "\n\nReply with one tool-call JSON object to continue, or your final answer as plain text."
    old = {"role": "user", "content": f"Result of probe_url:\n{BIG}{trailer}"}
    assistant = {"role": "assistant", "content": '{"tool": "probe_url", "arguments": {}}'}
    recent = {"role": "user", "content": f"Result of probe_url:\n{KEPT}{trailer}"}
    src = [FRAMING, old, assistant, recent]
    out = compact_context(src, "crucible", budget=1000, keep_recent=2)
    _assert_structure_preserved(out, src)
    assert out[1]["content"].startswith("Result of probe_url:\n")   # prefix line kept
    assert ELIDED in out[1]["content"] and BIG not in out[1]["content"]
    assert out[2] == assistant
    assert out[3]["content"] == f"Result of probe_url:\n{KEPT}{trailer}"   # recent untouched


# ---- boundary conditions ----------------------------------------------------

def test_under_budget_returns_unchanged():
    src = [FRAMING,
           {"role": "tool", "tool_call_id": "c1", "content": "small"},
           {"role": "assistant", "content": "ok"},
           {"role": "tool", "tool_call_id": "c2", "content": "also small"}]
    out = compact_context(src, "ollama", budget=10_000_000, keep_recent=2)
    assert out == src                                 # contents unchanged
    assert out[1]["content"] == "small" and out[3]["content"] == "also small"


def test_index_zero_is_never_shrunk_even_when_shrinkable():
    # A shrinkable-shaped message parked at index 0 is the (protected) first framing slot.
    src = [{"role": "tool", "tool_call_id": "first", "content": BIG},
           {"role": "tool", "tool_call_id": "old", "content": BIG},
           {"role": "assistant", "content": "x"},
           {"role": "tool", "tool_call_id": "recent", "content": KEPT}]
    out = compact_context(src, "ollama", budget=1000, keep_recent=2)
    assert out[0] == src[0] and out[0]["content"] == BIG   # index 0 never touched
    assert ELIDED in out[1]["content"]                     # the genuinely-old one shrank
    assert out[3]["content"] == KEPT                       # recent window untouched


def test_input_list_is_not_mutated():
    old = {"role": "tool", "tool_call_id": "old", "content": BIG}
    src = [FRAMING, old, {"role": "assistant", "content": "x"}, {"role": "tool", "tool_call_id": "r", "content": KEPT}]
    before = json.dumps(src, default=str)
    compact_context(src, "ollama", budget=1000, keep_recent=2)
    assert json.dumps(src, default=str) == before          # pure: caller's list/objects intact
    assert old["content"] == BIG


# ---- integration against the real _api loop (ollama, httpx stubbed) ---------

class _Resp:
    def __init__(self, payload): self._p, self.status_code = payload, 200
    def raise_for_status(self): pass
    def json(self): return self._p


TOOL_TURNS = 14          # tool-call turns before the final answer (loop cap is 20)
PAYLOAD = "Z" * 35000    # each tool call returns ~35 KB; uncompacted that is ~490 KB of context


class _BigClient:
    """httpx.Client stand-in: native tools, a huge tool result every turn, then a finish."""
    def __init__(self, *a, **k):
        self.seen, self.turns = [], 0
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def post(self, url, json=None, headers=None):
        if url.endswith("/api/show"):
            return _Resp({"capabilities": ["tools"]})      # native tool API available
        self.seen.append(json)                             # an /api/chat body (main loop only; no planner on a plain run)
        self.turns += 1
        if self.turns <= TOOL_TURNS:
            return _Resp({"message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"c{self.turns}", "function": {"name": "search_knowledge", "arguments": {}}}]},
                "usage": {}})
        return _Resp({"message": {"role": "assistant", "content": "Done investigating."}, "usage": {}})


def test_api_loop_keeps_working_context_bounded_and_completes(app, monkeypatch):
    captured = {}
    def factory(*a, **k):
        client = _BigClient(); captured["client"] = client; return client
    monkeypatch.setattr(providers.httpx, "Client", factory)
    # Every tool call returns a huge payload; call() is replaced so no real tool/store work runs.
    monkeypatch.setattr(ModelTools, "call", lambda self, name, arguments: {"payload": PAYLOAD})

    # A plain (non-agent) run: no scope gate and, crucially, no planner preamble, so every
    # captured body is a main-loop turn.
    run = app.store.put("run", {"project_id": "p", "status": "running", "provider": "ollama",
        "model": "m", "prompt": "go", "events": [],
        "settings_snapshot": {**DEFAULT_SETTINGS, "provider": "ollama", "model": "m"}})
    answer = app.runs._api(run, threading.Event())

    assert "Done investigating" in answer                  # the run completed cleanly

    bodies = captured["client"].seen
    assert len(bodies) == TOOL_TURNS + 1                   # one body per model call, nothing skipped

    # Nothing dropped/merged: body == [system] + working messages, and each turn adds exactly
    # one assistant + one tool-result message (so consecutive bodies differ by exactly 2).
    counts = [len(b["messages"]) for b in bodies]
    assert all(counts[i + 1] - counts[i] == 2 for i in range(len(counts) - 1))

    # The system/first framing message is identical in every body and never shrunk.
    systems = [b["messages"][0] for b in bodies]
    framings = [b["messages"][1] for b in bodies]
    assert all(s["role"] == "system" and s == systems[0] for s in systems)
    assert all(f == framings[0] and "Current project: p." in f["content"] for f in framings)

    # Compaction actually fired: an old tool result was elided in the context sent upstream.
    assert any(ELIDED in json.dumps(b, default=str) for b in bodies)

    # Bounded: the working context (everything after the prepended system message) stays far
    # below what retaining all TOOL_TURNS full results (~490 KB) would cost, and never blows up.
    working_sizes = [len(json.dumps(b["messages"][1:], default=str).encode()) for b in bodies]
    uncompacted_lower_bound = TOOL_TURNS * len(PAYLOAD)
    assert max(working_sizes) < uncompacted_lower_bound // 2
    assert max(working_sizes) < 300000

    # The durable transcript is likewise bounded and keeps every message (pairing intact).
    saved = app.store.get("run", run["id"])
    assert isinstance(saved.get("transcript"), list) and saved["transcript"]
    assert len(json.dumps(saved["transcript"], default=str).encode()) < 300000
    assert ELIDED in json.dumps(saved["transcript"], default=str)   # old results elided before persist too


def test_api_loop_default_budget_and_keep_recent_are_sane():
    # Guard the module knobs the loop relies on (chosen per the milestone spec).
    assert CONTEXT_COMPACT_BUDGET_BYTES == 180000
    assert COMPACT_KEEP_RECENT == 8
