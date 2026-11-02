"""Redact model/report context while preserving raw local request evidence."""
from __future__ import annotations

import re
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_KEY = re.compile(r"(?:authorization|proxy.authorization|cookie|set.cookie|password|passwd|secret|token|api.?key|session(?:id|_id)?|csrf)", re.I)


def redact_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(("{","[")):
        try:
            parsed = json.loads(text)
            return json.dumps(redact(parsed, credentials_context=True),ensure_ascii=False)
        except (ValueError,RecursionError):
            pass
    text = re.sub(r"(?im)^(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*[^\r\n]+", r"\1: [REDACTED]", text)
    names = r"(?:password|passwd|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|csrf[_-]?token|session[_-]?id)"
    text = re.sub(r"(?i)([\"']?"+names+r"[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",lambda m:m[1]+m[2]+"[REDACTED]"+m[2],text)
    text = re.sub(r"(?i)("+names+r"\s*=\s*)(?![\"'])[^&\r\n]+",r"\1[REDACTED]",text)
    text = re.sub(r"\b(?:sk-ant-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[REDACTED]", text)
    return text


def redact(value, key: str = "", credentials_context: bool = False):
    metadata = {"session_id", "session_ids", "token_count", "input_tokens", "output_tokens"}
    if SECRET_KEY.search(key) and (credentials_context or key not in metadata):
        return "[REDACTED]"
    if isinstance(value, dict):
        if "name" in value and "value" in value and SECRET_KEY.search(str(value["name"])):
            return {**value,"value":"[REDACTED]"}
        return {k: redact(v,k,credentials_context or key in {"headers","cookies","body"}) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v,credentials_context=credentials_context) for v in value]
    if isinstance(value, str):
        value = redact_text(value)
        if key in {"url", "base_url"}:
            try:
                u = urlsplit(value)
                pairs = [(k,"[REDACTED]" if SECRET_KEY.search(k) else v) for k,v in parse_qsl(u.query,keep_blank_values=True)]
                return urlunsplit((u.scheme,u.netloc.split("@")[-1],u.path,urlencode(pairs),u.fragment))
            except ValueError:
                return "[invalid URL]"
        return value
    return value
