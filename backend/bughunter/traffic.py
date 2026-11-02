"""Traffic ingestion. Imported messages are evidence, never instructions."""
from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from .store import Store

MAX_IMPORT = 16 * 1024 * 1024


def parse_raw(content: str, base_url: str | None = None) -> dict:
    head, separator, body = content.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    if not lines:
        raise ValueError("The HTTP request is empty")
    match = re.fullmatch(r"([A-Z]+) (\S+) HTTP/(?:1\.[01]|2(?:\.0)?)", lines[0])
    if not match:
        raise ValueError("Expected an HTTP request line, for example GET / HTTP/1.1")
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise ValueError("Malformed HTTP header")
        key = key.strip().lower()
        headers[key] = f"{headers[key]}, {value.strip()}" if key in headers else value.strip()
    method, target = match.groups()
    if target.startswith(("http://", "https://")):
        url = target
    else:
        if not base_url:
            host = headers.get("host")
            if not host:
                raise ValueError("Supply base_url or a Host header")
            base_url = "https://" + host
        url = urljoin(base_url.rstrip("/") + "/", target)
    return {"method": method, "url": url, "headers": headers, "body": body if separator else ""}


def parse_response(raw: str) -> dict:
    head, _, body = raw.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    if not lines:
        return {"status": None, "headers": {}, "body": ""}
    match = re.match(r"HTTP/\S+ (\d{3})", lines[0])
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.lower()] = value.strip()
    return {"status": int(match[1]) if match else None, "headers": headers, "body": body}


def inventory(request: dict) -> dict:
    from urllib.parse import parse_qs
    url = urlsplit(request.get("url", ""))
    fields = [{"location": "query", "name": k, "value": v[-1]} for k, v in parse_qs(url.query, keep_blank_values=True).items()]
    body = request.get("body", "")
    try:
        value = json.loads(body)
        def walk(obj, path=""):
            if isinstance(obj, dict):
                for key, item in obj.items(): walk(item, f"{path}.{key}".strip("."))
            elif isinstance(obj, list):
                for i, item in enumerate(obj): walk(item, f"{path}[{i}]")
            else: fields.append({"location": "json", "name": path, "value": obj})
        walk(value)
    except (ValueError, TypeError):
        if "application/x-www-form-urlencoded" in str(request.get("headers", {})).lower():
            fields += [{"location": "form", "name": k, "value": v[-1]} for k, v in parse_qs(body, keep_blank_values=True).items()]
    for key in request.get("headers", {}):
        fields.append({"location": "header", "name": key, "value": "[present]"})
    return {"host": url.hostname, "path": url.path, "method": request.get("method"), "fields": fields,
            "protocol": "graphql" if "graphql" in url.path.lower() or '"query"' in body else "http"}


class TrafficService:
    def __init__(self, store: Store): self.store = store

    def list(self, project_id: str | None = None):
        return self.store.list("traffic", **({"project_id": project_id} if project_id else {}))

    def get(self, id: str):
        item = self.store.get("traffic", id)
        if not item: raise ValueError("Traffic item not found")
        return item

    def record(self, project_id: str, request: dict, response: dict | None = None, session_id: str = "default", **extra):
        if not self.store.get("project", project_id): raise ValueError("Project not found")
        return self.store.put("traffic", {"project_id": project_id, "request": request, "response": response,
                              "session_id": session_id, "inventory": inventory(request), **extra})

    def import_data(self, project_id: str, format: str, content: str, base_url: str | None = None, session_id: str = "default"):
        if not self.store.get("project", project_id): raise ValueError("Project not found")
        if len(content.encode()) > MAX_IMPORT: raise ValueError("Traffic imports are limited to 16 MiB")
        items = []
        if format == "raw":
            pairs = [(parse_raw(content, base_url), None)]
        elif format == "har":
            try: entries = json.loads(content)["log"]["entries"]
            except (ValueError, KeyError, TypeError) as exc: raise ValueError("Invalid HAR document") from exc
            if not isinstance(entries, list) or len(entries) > 10000: raise ValueError("Invalid or oversized HAR entries")
            pairs = []
            for entry in entries:
                req, res = entry["request"], entry.get("response", {})
                headers = {h["name"].lower(): h["value"] for h in req.get("headers", [])}
                request = {"method": req["method"], "url": req["url"], "headers": headers, "body": req.get("postData", {}).get("text", "")}
                text = res.get("content", {}).get("text", "")
                if res.get("content", {}).get("encoding") == "base64":
                    try: text = base64.b64decode(text, validate=True).decode("utf-8", "replace")
                    except ValueError: text = "[Invalid base64 response]"
                response = {"status": res.get("status"), "headers": {h["name"].lower(): h["value"] for h in res.get("headers", [])}, "body": text}
                pairs.append((request, response))
        elif format == "burp_xml":
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", content, re.I): raise ValueError("DTD and entity declarations are not accepted")
            try: root = ET.fromstring(content)
            except ET.ParseError as exc: raise ValueError("Invalid Burp XML") from exc
            pairs = []
            for item in root.findall(".//item"):
                def raw(name):
                    element = item.find(name)
                    if element is None: return ""
                    text = element.text or ""
                    return base64.b64decode(text, validate=True).decode("utf-8", "replace") if element.get("base64") == "true" else text
                request = parse_raw(raw("request"), item.findtext("url") or base_url)
                pairs.append((request, parse_response(raw("response"))))
        else:
            raise ValueError("Supported formats are raw, har, and burp_xml")
        for request, response in pairs:
            items.append(self.record(project_id, request, response, session_id, origin="imported", trusted_observation=False))
        return {"imported": len(items), "items": items}

    def dispatch(self, method: str, params: dict):
        if method == "traffic.list": return self.list(**params)
        if method == "traffic.get": return self.get(**params)
        if method == "traffic.import": return self.import_data(**params)
        raise ValueError("Unknown traffic operation")
