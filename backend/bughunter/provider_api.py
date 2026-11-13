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
        "gemini":"https://generativelanguage.googleapis.com/v1beta/openai",
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
                  timeout: int = 180, check=None) -> dict:
    if check:
        import asyncio,httpx
        from .inference_gate import local_inference
        async def request():
            async with httpx.AsyncClient(timeout=timeout,trust_env=False,follow_redirects=False) as client:
                response=await client.post(url,json=payload,headers=_headers(settings))
                if response.is_error:raise RuntimeError(f'provider HTTP {response.status_code}: '+response.text[:500])
                return response.json()
        async def cancellable():
            task=asyncio.create_task(request())
            try:
                while not task.done():check();await asyncio.wait({task},timeout=.1)
                check();return await task
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        with local_inference(check,enabled=settings.get('provider')=='ollama'):
            return asyncio.run(cancellable())
    if settings.get("provider") == "ollama":
        import httpx
        from .inference_gate import GatedClient
        from .store import Store
        store = Store()
        def check():
            if store.setting('emergency_stop',False): raise InterruptedError('Local inference stopped by operator.')
        with httpx.Client(timeout=timeout,trust_env=False,follow_redirects=False) as client:
            response = GatedClient(client,check).post(url,json=payload,headers=_headers(settings))
            response.raise_for_status()
            return response.json()
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
                      max_tokens: int = 1024,check=None) -> str:
    """One-shot plain completion. Returns assistant text."""
    response=single_tool_turn(provider_cfg=settings,transcript=[{'role':'system','content':'Answer the user’s request directly. No tools are available for this text-only generation.'},{'role':'user','content':prompt}],tool_specs=[],max_tokens=max_tokens,check=check)
    return (response.get('message') or {}).get('content') or ''


def single_tool_turn(*, provider_cfg: dict, transcript: list[dict],
                     tool_specs: list[dict], max_tokens: int = 2048, check=None,execute=None) -> dict:
    """One assistant turn with tool-calling for OpenAI-compatible endpoints.

    Returns ``{"message": <assistant_msg>, "tool_calls": [{id, name, input}]}``.
    Falls back to the prompt-level fallback_tool_protocol when the model
    rejects native tools (DeepSeek-reasoner and some Ollama builds).
    """
    if check: check()
    from .providers import LOCAL_PROVIDERS,SELF_HOSTED_PROVIDERS,SUBSCRIPTION_PROVIDERS
    provider=provider_cfg.get('provider')
    from .conversation_memory import compact_messages
    budget=8000 if provider in LOCAL_PROVIDERS else 60000
    systems=[dict(m) for m in transcript if m.get('role')=='system']
    if provider in LOCAL_PROVIDERS:
        systems=systems[:1]+systems[-2:] if len(systems)>3 else systems
        for index,item in enumerate(systems):
            content=str(item.get('content',''))
            size=5500 if index==0 else 2500
            if len(content)>size:item['content']=content[:size//2]+'\n[Older context shortened; use shared memory and exact source tools.]\n'+content[-size//2:]
        tool_specs=[{**s,'description':str(s.get('description',''))[:220]} for s in tool_specs]
    transcript=systems+compact_messages(transcript,budget=budget)
    if provider_cfg.get('local_only') and provider not in LOCAL_PROVIDERS:raise ValueError('Local-only mode blocks the selected hosted provider.')
    if provider not in SELF_HOSTED_PROVIDERS|SUBSCRIPTION_PROVIDERS:
        if not provider_cfg.get('pricing_confirmed'):raise ValueError('Confirm this API connection’s pricing in Settings first.')
        estimate=(len(json.dumps(transcript))//3*float(provider_cfg.get('input_price_per_million',0))+max_tokens*float(provider_cfg.get('output_price_per_million',0)))/1_000_000
        if estimate>float(provider_cfg.get('api_budget_usd',0)):raise ValueError('This reply exceeds the configured API budget.')
    if provider_cfg.get('provider') in {'codex','claude_code','gemini_cli','anthropic'}:
        return _routed_tool_turn(provider_cfg, transcript, tool_specs, max_tokens, check,execute)
    base = _base_url(provider_cfg)
    if not base: raise ValueError('Configure the selected model endpoint in Settings.')
    from urllib.parse import urlsplit
    from .providers import is_local_network_host
    endpoint=urlsplit(base)
    if endpoint.scheme not in {'http','https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:raise ValueError('Use a valid model endpoint without URL credentials or a query.')
    if endpoint.scheme=='http' and endpoint.hostname not in {'localhost','127.0.0.1','::1'} and not (provider=='crucible' and is_local_network_host(endpoint.hostname)):raise ValueError('Remote model endpoints require HTTPS.')
    if provider=='crucible' and not is_local_network_host(endpoint.hostname):raise ValueError('Crucible endpoints must be local.')
    model = provider_cfg.get("model", "")
    tools_formatted = [
        {"type": "function",
         "function": {"name": s["name"],
                      "description": s.get("description", ""),
                      "parameters": s.get("input_schema", s.get('parameters', {"type": "object"}))}}
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
    if not tools_formatted:payload.pop('tools');payload.pop('tool_choice')
    if provider=='openai':
        payload['max_completion_tokens']=payload.pop('max_tokens')
        if provider_cfg.get('reasoning_effort'):payload.pop('temperature',None)
    if provider_cfg.get('reasoning_effort') and provider_cfg.get('provider') in {'openai','gemini'}:
        payload['reasoning_effort']=provider_cfg['reasoning_effort']
    # DeepSeek-reasoner and some local builds reject 'tools'.
    try:
        response = _request_json(base + "/chat/completions", payload, provider_cfg,check=check)
    except RuntimeError as exc:
        if "tools" in str(exc).lower():
            return _fallback_tool_turn(provider_cfg, transcript, tool_specs,
                                       max_tokens,check)
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


def _routed_tool_turn(settings, transcript, specs, max_tokens, check=None,execute=None):
    """Subscriptions reason; only the application executes the returned tool call."""
    from .providers import fallback_tool_protocol, parse_fallback_call
    from .store import new_id
    system='\n\n'.join(str(m.get('content','')) for m in transcript if m.get('role')=='system')
    normalized=[{**s,'parameters':s.get('input_schema',s.get('parameters',{}))} for s in specs]
    native=settings.get('provider')=='codex' and execute is not None
    if not native and normalized:
        system+='\n\n'+fallback_tool_protocol(normalized)
        system+='\nThis JSON is an application RPC message executed by BugHunter after your reply. It does not require a CLI tool. Return the requested JSON to use the host capability, rather than claiming the host cannot execute it.'
        system+='\nExact tool argument schemas:\n'+json.dumps(normalized)
    messages=[{'role':'system','content':system}]
    for msg in transcript:
        if msg.get('role')=='system': continue
        content=str(msg.get('content') or '')
        if msg.get('tool_calls'): content+='\nRequested tools: '+json.dumps(msg['tool_calls'])
        role=msg.get('role')
        if role=='tool':role='user';content='UNTRUSTED TOOL RESULT '+str(msg.get('name',''))+':\n'+content
        if role in {'user','assistant'}:messages.append({'role':role,'content':content})
    if settings.get('provider')=='codex':
        from .codex_subscription import CodexSession
        prompt='\n\n'.join(m['role'].upper()+': '+m['content'] for m in messages[1:])
        with CodexSession(timeout=180,cancel_check=check) as session:
            kwargs={'specs':normalized,'tool_handler':execute,'max_calls':min(int(settings.get('max_agent_turns',120) or 120),300),'final_only':True} if native else {}
            result=session.run(settings['model'],system,prompt,reasoning_effort=settings.get('reasoning_effort') or None,**kwargs)
    elif settings.get('provider')=='claude_code':
        from . import process_runtime
        from .providers import subscription_env
        import shutil, tempfile
        executable=shutil.which('claude')
        if not executable:raise ValueError('Install Claude Code and sign in from Settings.')
        args=[executable,'--print','--output-format','text','--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','','--settings','{"disableAllHooks":true}','--safe-mode','--no-session-persistence','--max-turns','1','--permission-mode','dontAsk','--model',settings['model'],'--system-prompt',system]
        if settings.get('reasoning_effort'):args+=['--effort',settings['reasoning_effort']]
        with tempfile.TemporaryDirectory(prefix='bh-harness-claude-') as workspace:
            reply=process_runtime.run(args,cwd=workspace,env=subscription_env(),timeout=180,check=check,
                input='\n\n'.join(m['role'].upper()+': '+m['content'] for m in messages[1:]))
        if reply['exit_code']:raise ValueError('Claude reply failed: '+reply['stderr'][-600:])
        result=reply['stdout']
    elif settings.get('provider')=='gemini_cli':
        from .gemini_subscription import complete
        result=complete(messages,system,settings['model'],settings.get('reasoning_effort',''),cancel_check=check)
    else:
        # Anthropic's native message shape; use the same cancellable transport.
        from .providers import keyring
        key=keyring.get_password('BugHunterClaudex','anthropic')
        if not key:raise ValueError('Save the Anthropic API key in Settings first.')
        base=(settings.get('api_base_url') or 'https://api.anthropic.com').rstrip('/')
        from urllib.parse import urlsplit
        parsed=urlsplit(base)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Use a valid HTTPS Anthropic endpoint.')
        import asyncio,httpx
        async def request():
            async with httpx.AsyncClient(timeout=180,trust_env=False,follow_redirects=False) as client:
                payload={'model':settings['model'],'system':system,'messages':messages[1:],'max_tokens':max_tokens}
                if settings.get('reasoning_effort'):payload['output_config']={'effort':settings['reasoning_effort']}
                response=await client.post(base+('/messages' if base.endswith('/v1') else '/v1/messages'),json=payload,headers={'x-api-key':key,'anthropic-version':'2023-06-01'})
                response.raise_for_status();return '\n'.join(b.get('text','') for b in response.json()['content'] if b.get('type')=='text')
        async def cancellable():
            task=asyncio.create_task(request())
            try:
                while not task.done():
                    if check:check()
                    await asyncio.wait({task},timeout=.1)
                return await task
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        result=asyncio.run(cancellable())
    if check:check()
    if not isinstance(result,str) or not result.strip():raise ValueError('The selected model returned an empty reply.')
    call=None if native or not normalized else parse_fallback_call(result)
    if call:
        call_id=new_id('tc');name=call['tool'];args=call.get('arguments') or {}
        if name not in {s['name'] for s in specs} or not isinstance(args,dict):raise ValueError('The model proposed an invalid tool call.')
        raw=[{'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]
        return {'message':{'role':'assistant','content':'','tool_calls':raw},'tool_calls':[{'id':call_id,'name':name,'input':args}]}
    return {'message':{'role':'assistant','content':result},'tool_calls':[]}


def _fallback_tool_turn(provider_cfg: dict, transcript: list[dict],
                        tool_specs: list[dict], max_tokens: int,check=None) -> dict:
    from .providers import fallback_tool_protocol, parse_fallback_call
    system_add = fallback_tool_protocol(tool_specs)+'\nExact argument schemas:\n'+json.dumps(tool_specs)
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
    response = _request_json(base + "/chat/completions", payload, provider_cfg,check=check)
    try:
        text = response["choices"][0]["message"]["content"] or ""
    except Exception:
        text = ""
    call = parse_fallback_call(text)
    assistant_msg = {"role": "assistant", "content": text}
    if call:
        from .store import new_id
        call_id=new_id('tc')
        assistant_msg={'role':'assistant','content':'','tool_calls':[{'id':call_id,'type':'function','function':{'name':call['tool'],'arguments':json.dumps(call.get('arguments') or {})}}]}
        return {"message": assistant_msg,
                "tool_calls": [{"id": call_id,
                                "name": call['tool'],
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
