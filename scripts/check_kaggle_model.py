"""Check the saved Kaggle connection; --smoke runs two harmless inference turns.

Reads settings without modifying them. Sends no course, website or process data.
The only executable tool returns a fresh random test value held by this script.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time

import httpx
import keyring

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from bughunter.providers import DEFAULT_SETTINGS, probe_provider

TOOL = {"name":"read_check_value", "description":"Read the temporary connection-test value.",
        "parameters":{"type":"object", "properties":{}, "required":[], "additionalProperties":False}}
SUPPORTED = {"openai_compatible", "remote_ollama"}


def load_settings(root: Path):
    path = root.expanduser().resolve() / "workbench.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM settings WHERE key='settings'").fetchone()
    return {**DEFAULT_SETTINGS, **(json.loads(row[0]) if row else {})}


def smoke(settings, key, client):
    if settings.get("provider") not in SUPPORTED or settings.get("local_only"):
        raise ValueError("Select the Kaggle provider and disable local-only inference in Settings first.")
    if not key:
        raise ValueError("The server's saved connection key is missing.")
    ollama = settings["provider"] == "remote_ollama"
    endpoint = settings["api_base_url"].rstrip("/") + ("/api/chat" if ollama else "/chat/completions")
    marker = "connection-check-" + secrets.token_hex(16)
    messages = [
        {"role":"system", "content":"Connection check. Call read_check_value once with empty arguments, then repeat exactly the value it returns. Do not invent a value or request any other tool."},
        {"role":"user", "content":"Read and report the temporary check value."},
    ]
    started = time.monotonic()

    def infer():
        payload = {"model":settings["model"], "messages":messages, "stream":False,
                   "tools":[{"type":"function", "function":TOOL}]}
        if ollama:
            payload.update(options={"num_predict":256, "temperature":0}, think=False)
        else:
            payload.update(max_tokens=256, temperature=0)
        response = client.post(endpoint, headers={"Authorization":f"Bearer {key}"}, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["message"] if ollama else data["choices"][0]["message"]

    first = infer()
    calls = first.get("tool_calls") or []
    if len(calls) != 1:
        raise ValueError("Expected exactly one native tool call; the model did not return one.")
    call = calls[0]
    function = call.get("function") or {}
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if function.get("name") != TOOL["name"] or arguments != {}:
        raise ValueError("The model requested an unsupported tool or arguments. Nothing was executed.")
    if not ollama and not isinstance(call.get("id"), str):
        raise ValueError("The native tool call is missing its correlation ID.")
    # Dispatch only the fixed local read above. No eval, shell, RPC or dynamic tools.
    messages.append({"role":"assistant", "content":first.get("content") or "", "tool_calls":calls})
    messages.append({"role":"tool", "content":json.dumps({"value":marker}),
                     **({"tool_name":TOOL["name"]} if ollama else {"tool_call_id":call["id"]})})
    final = infer()
    if final.get("tool_calls") or marker not in (final.get("content") or ""):
        raise ValueError("The final answer was not grounded in the tool's returned value.")
    return {"passed":True, "tool":TOOL["name"], "round_trips":2,
            "seconds":round(time.monotonic()-started, 2), "target_requests":0,
            "limits":"Native tool protocol only; no game, Burp, media accuracy or broad task competence was tested."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.home()/"Library/Application Support/BugHunterClaudex")
    parser.add_argument("--smoke", action="store_true", help="Run two inference requests against the selected remote server.")
    args = parser.parse_args()
    settings = load_settings(args.data_dir)
    if settings["provider"] not in SUPPORTED:
        print(json.dumps({"ok":False, "provider":settings["provider"], "detail":"Kaggle is not selected. Save its endpoint, model and key in the app first."}))
        return 2
    if settings.get("local_only"):
        raise ValueError("Local-only inference blocks Kaggle. Select the remote provider in Settings first.")
    # Validate the URL before retrieving or transmitting a credential, including
    # for remote_ollama whose legacy metadata probe accepts fewer URL checks.
    from urllib.parse import urlsplit
    parsed = urlsplit(settings.get("api_base_url") or "")
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Configure a valid HTTPS API URL without credentials or query parameters.")
    _ = parsed.port
    probe = probe_provider(settings)
    print(json.dumps(probe), flush=True)
    if not probe.get("ok"):
        return 1
    if args.smoke:
        key = keyring.get_password("BugHunterClaudex", settings["provider"])
        with httpx.Client(timeout=httpx.Timeout(210, connect=8), trust_env=False, follow_redirects=False) as client:
            print(json.dumps({"smoke":smoke(settings, key, client)}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError, IndexError, sqlite3.Error, httpx.HTTPError, keyring.errors.KeyringError) as error:
        # Never echo provider response bodies, credentials, URLs or prompt text.
        print(json.dumps({"ok":False, "error_type":type(error).__name__,
                          "detail":"Connection check failed. Check the saved configuration and model's native tool support."}), file=sys.stderr)
        raise SystemExit(1)
