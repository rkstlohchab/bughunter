"""Minimal provider API helpers for the operator/forge subsystems.

These are a narrow slice of what ``providers.RunManager`` does:
one ``single_tool_turn`` for OpenAI-compatible endpoints (covers Ollama,
OpenAI, DeepSeek, remote-Ollama, Crucible, openai_compatible); plus a
``simple_completion`` for plain text generation (used by the distiller).

Full provider routing — Anthropic, Claude Code CLI, ``crucible`` quirks —
still lives in ``providers.py``.  These helpers are used only by code
paths where a tiny chat turn is enough.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


def _base_url(settings: dict) -> str:
    provider = (settings.get("provider") or "").lower()
    base = (settings.get("api_base_url") or "").rstrip("/")
    defaults = {
        "ollama": "http://127.0.0.1:11434/v1",
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "crucible": "http://127.0.0.1:8080/v1",
    }
    if not base:
        base = defaults.get(provider, "")
    if provider in {"ollama", "remote_ollama"} and base and not base.endswith("/v1"):
        base += "/v1"
    return base


def _api_key(settings: dict) -> str:
    provider = (settings.get("provider") or "").lower()
    try:
        from . import providers as P
        value = P.keyring.get_password("BugHunterClaudex", provider) or ""
    except Exception:
        value = ""
    return value or "local"


def _headers(settings: dict) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_api_key(settings)}",
    }


def _request_json(url: str, payload: dict, settings: dict,
                  timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(settings),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rsp:
            return json.loads(rsp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {e.code}: {body[:500]}") from e


def simple_completion(settings: dict, prompt: str,
                      max_tokens: int = 1024) -> str:
    """One-shot plain completion. Returns assistant text."""
    base = _base_url(settings)
    if not base:
        raise RuntimeError("provider base URL not configured")
    model = settings.get("model", "")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": settings.get("temperature", 0.3),
    }
    response = _request_json(base + "/chat/completions", payload, settings)
    try:
        return response["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


def single_tool_turn(*, provider_cfg: dict, transcript: list[dict],
                     tool_specs: list[dict], max_tokens: int = 2048) -> dict:
    """One assistant turn with tool-calling for OpenAI-compatible endpoints.

    Returns ``{"message": <assistant_msg>, "tool_calls": [{id, name, input}]}``.
    Falls back to the prompt-level fallback_tool_protocol when the model
    rejects native tools (DeepSeek-reasoner and some Ollama builds).
    """
    base = _base_url(provider_cfg)
    model = provider_cfg.get("model", "")
    tools_formatted = [
        {"type": "function",
         "function": {"name": s["name"],
                      "description": s.get("description", ""),
                      "parameters": s.get("input_schema", {"type": "object"})}}
        for s in tool_specs
    ]
    payload = {
        "model": model,
        "messages": _coerce_messages(transcript),
        "tools": tools_formatted,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": provider_cfg.get("temperature", 0.3),
    }
    # DeepSeek-reasoner and some local builds reject 'tools'.
    try:
        response = _request_json(base + "/chat/completions", payload, provider_cfg)
    except RuntimeError as exc:
        if "tools" in str(exc).lower():
            return _fallback_tool_turn(provider_cfg, transcript, tool_specs,
                                       max_tokens)
        raise
    try:
        choice = response["choices"][0]
        msg = choice["message"]
    except Exception:
        return {"message": None, "tool_calls": []}
    tool_calls_raw = msg.get("tool_calls") or []
    tool_calls = []
    for tc in tool_calls_raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        tool_calls.append({
            "id": tc.get("id") or "",
            "name": fn.get("name") or "",
            "input": args,
        })
    assistant_msg = {
        "role": "assistant",
        "content": msg.get("content") or "",
        "tool_calls": tool_calls_raw,
    }
    return {"message": assistant_msg, "tool_calls": tool_calls}


def _fallback_tool_turn(provider_cfg: dict, transcript: list[dict],
                        tool_specs: list[dict], max_tokens: int) -> dict:
    from .providers import fallback_tool_protocol, parse_fallback_call
    system_add = fallback_tool_protocol(tool_specs)
    messages = list(transcript)
    if messages and messages[0].get("role") == "system":
        messages[0] = {"role": "system",
                       "content": (messages[0].get("content", "") + "\n" + system_add)}
    else:
        messages.insert(0, {"role": "system", "content": system_add})
    base = _base_url(provider_cfg)
    model = provider_cfg.get("model", "")
    payload = {"model": model, "messages": _coerce_messages(messages),
               "max_tokens": max_tokens,
               "temperature": provider_cfg.get("temperature", 0.3)}
    response = _request_json(base + "/chat/completions", payload, provider_cfg)
    try:
        text = response["choices"][0]["message"]["content"] or ""
    except Exception:
        text = ""
    call = parse_fallback_call(text)
    assistant_msg = {"role": "assistant", "content": text}
    if call:
        return {"message": assistant_msg,
                "tool_calls": [{"id": "fb_" + call.get("name", "unknown"),
                                "name": call.get("name"),
                                "input": call.get("arguments") or {}}]}
    return {"message": assistant_msg, "tool_calls": []}


def _coerce_messages(transcript: list[dict]) -> list[dict]:
    """Coerce the internal transcript to OpenAI-compatible messages."""
    out = []
    for msg in transcript:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        copy = {"role": role, "content": msg.get("content") or ""}
        # preserve tool call blocks on assistant
        if role == "assistant" and msg.get("tool_calls"):
            copy["tool_calls"] = msg["tool_calls"]
        # tool results need tool_call_id
        if role == "tool":
            if msg.get("tool_call_id"):
                copy["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                copy["name"] = msg["name"]
        out.append(copy)
    return out
