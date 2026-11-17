"""Web access-log parsers normalised to one event shape, with format auto-detection.

Supported, by detection on sample lines:

* ``combined``  nginx/Apache common and combined formats (optional leading vhost)
* ``alb``       AWS Application Load Balancer access logs
* ``w3c``       W3C extended logs with a ``#Fields:`` header (CloudFront, IIS, Azure App Service)
* ``json``      one JSON object per line: Vercel/Netlify log drains, Cloudflare HTTP
                requests and firewall events, GCP HTTP(S) load balancer, Azure Front Door,
                and generic app loggers. Keys are matched by alias, including nested
                ``httpRequest``/``request``/``proxy`` objects.

A line that does not parse is counted, never guessed at.
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from urllib.parse import urlsplit

COMBINED = re.compile(
    r'^(?:(?P<vhost>[^\s"]+)\s+)??(?P<ip>[0-9a-fA-F:.]+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+(?P<status>\d{3}|-)\s+(?P<size>\d+|-)'
    r'(?:\s+"(?P<referer>[^"]*)"\s+"(?P<ua>[^"]*)")?')
ALB = re.compile(
    r'^(?P<type>h2|http|https|ws|wss|grpcs)\s+(?P<time>\S+)\s+\S+\s+(?P<ip>[0-9a-fA-F:.]+):\d+\s+\S+\s+'
    r'\S+\s+(?P<duration>\S+)\s+\S+\s+(?P<status>\d{3}|-)\s+\S+\s+\S+\s+(?P<size>\d+)\s+'
    r'"(?P<request>[^"]*)"\s+"(?P<ua>[^"]*)"')

_ALIASES = {
    "time": ("timestamp", "time", "@timestamp", "date", "datetime", "EdgeStartTimestamp", "Datetime", "receiveTimestamp",
             "TimeGenerated", "ts"),
    "ip": ("ClientIP", "clientIp", "client_ip", "remote_addr", "remoteAddr", "remoteIp", "ip", "clientIP", "source_ip",
           "x_forwarded_for", "realIp", "clientIpAddress", "c-ip"),
    "method": ("ClientRequestMethod", "method", "requestMethod", "http_method", "httpMethod", "verb"),
    "path": ("ClientRequestPath", "path", "requestPath", "uri", "url", "requestUrl", "ClientRequestURI", "request_uri",
             "requestUri", "pathname", "cs-uri-stem"),
    "query": ("ClientRequestQuery", "query", "queryString", "query_string", "cs-uri-query", "search"),
    "status": ("EdgeResponseStatus", "OriginResponseStatus", "status", "statusCode", "status_code", "responseStatusCode",
               "httpStatusCode", "sc-status", "proxyStatusCode"),
    "size": ("EdgeResponseBytes", "size", "bytes", "responseSize", "body_bytes_sent", "sc-bytes", "responseBytes"),
    "ua": ("ClientRequestUserAgent", "userAgent", "user_agent", "http_user_agent", "ua", "cs(User-Agent)"),
    "host": ("ClientRequestHost", "host", "hostname", "requestHost", "domain", "cs-host", "x-host-header"),
    "referer": ("ClientRequestReferer", "referer", "referrer", "http_referer", "cs(Referer)"),
    "action": ("Action", "action", "wafAction", "firewall_action"),
    "rule": ("RuleID", "ruleId", "rule_id", "Description", "ruleName"),
}
_NESTED = ("httpRequest", "request", "proxy", "http", "properties", "req", "message")


def _time(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1e9 if value > 1e14 else value / 1e3 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    text = str(value)
    for fmt in ("%d/%b/%Y:%H:%M:%S %z",):
        try:
            return datetime.strptime(text, fmt).astimezone(UTC).isoformat()
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_target(target: str) -> tuple[str, str]:
    if target.startswith(("http://", "https://")):
        parts = urlsplit(target)
        return parts.path or "/", parts.query
    path, _, query = target.partition("?")
    return path or "/", query


def _event(ip, method, target, status, size, ua="", time=None, host="", referer="", **extra) -> dict | None:
    if not ip or not target:
        return None
    path, query = _split_target(target)
    return {"time": time, "ip": ip.split(",")[0].strip(), "method": (method or "-").upper()[:16], "path": path[:2048],
            "query": query[:4096], "status": _int(status), "size": _int(size), "ua": (ua or "")[:512],
            "host": (host or "")[:256], "referer": (referer or "")[:1024], **extra}


def _request_line(request: str) -> tuple[str, str]:
    parts = request.split(" ")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "-", request


def parse_combined(line: str) -> dict | None:
    match = COMBINED.match(line)
    if not match:
        return None
    method, target = _request_line(match["request"])
    return _event(match["ip"], method, target, match["status"], match["size"], match["ua"] or "",
                  _time(match["time"]), match["vhost"] or "", match["referer"] or "")


def parse_alb(line: str) -> dict | None:
    match = ALB.match(line)
    if not match:
        return None
    method, target = _request_line(match["request"])
    host = urlsplit(target).hostname or "" if target.startswith("http") else ""
    return _event(match["ip"], method, target, match["status"], match["size"], match["ua"], _time(match["time"]), host)


def _lookup(record: dict, key: str):
    for source in (record, *[record.get(n) for n in _NESTED if isinstance(record.get(n), dict)]):
        for alias in _ALIASES[key]:
            if alias in source and source[alias] not in (None, ""):
                return source[alias]
    return None


def parse_json(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    target = _lookup(record, "path")
    query = _lookup(record, "query")
    if target is not None and query and "?" not in str(target):
        target = f"{target}?{str(query).lstrip('?')}"
    event = _event(str(_lookup(record, "ip") or ""), str(_lookup(record, "method") or "-"), str(target or ""),
                   _lookup(record, "status"), _lookup(record, "size"), str(_lookup(record, "ua") or ""),
                   _time(_lookup(record, "time")), str(_lookup(record, "host") or ""), str(_lookup(record, "referer") or ""))
    if event is not None:
        action, rule = _lookup(record, "action"), _lookup(record, "rule")
        if action:
            event["waf_action"] = str(action)[:64]
        if rule:
            event["waf_rule"] = str(rule)[:200]
    return event


class W3C:
    def __init__(self, fields: list[str]):
        self.fields = fields

    def parse(self, line: str) -> dict | None:
        values = line.split("\t") if "\t" in line else shlex.split(line) if '"' in line else line.split()
        if len(values) < len(self.fields):
            return None
        row = dict(zip(self.fields, values))
        get = lambda *names: next((row[n] for n in names if row.get(n) not in (None, "-")), None)
        target = get("cs-uri-stem") or ""
        query = get("cs-uri-query")
        if query:
            target += "?" + query
        stamp = " ".join(v for v in (get("date"), get("time")) if v)
        return _event(get("c-ip") or "", get("cs-method"), target, get("sc-status"), get("sc-bytes"),
                      (get("cs(User-Agent)") or "").replace("%20", " "), _time(stamp.replace(" ", "T") + "Z" if stamp else None),
                      get("cs-host", "x-host-header") or "", get("cs(Referer)") or "")


def detect_format(lines: list[str]) -> tuple[str, object]:
    """Return (name, parser) for the format most sample lines parse as."""
    for line in lines[:50]:
        if line.startswith("#Fields:"):
            return "w3c", W3C(line.split(":", 1)[1].split())
    sample = [line for line in lines[:200] if line.strip() and not line.startswith("#")]
    if not sample:
        raise ValueError("No log lines to detect a format from.")
    best = ("", None, 0)
    for name, parser in (("json", parse_json), ("alb", parse_alb), ("combined", parse_combined)):
        hits = sum(1 for line in sample if parser(line))
        if hits > best[2]:
            best = (name, parser, hits)
    if best[2] < max(1, len(sample) // 2):
        raise ValueError("Unrecognised log format: fewer than half of the sample lines parse as JSON, ALB, W3C or "
                         "nginx/Apache combined logs.")
    return best[0], best[1]


def parser_for(name: str, header: str | None = None):
    if name == "w3c":
        if not header:
            raise ValueError("W3C logs need their #Fields header.")
        return W3C(header.split(":", 1)[1].split())
    return {"json": parse_json, "alb": parse_alb, "combined": parse_combined}[name]


def parse(parser, line: str) -> dict | None:
    if not line.strip() or line.startswith("#"):
        return None
    return parser.parse(line) if isinstance(parser, W3C) else parser(line)
