"""Attack detection over normalised access-log events.

Signatures look at one request; behaviours look at one client's requests
over a time window. Every technique is tied to the TBHM section an attacker
would be following, so a detection reads as "someone is doing content
discovery against you" rather than as a bare regex hit.

Access logs carry the request line, not the body. POST-body attacks (most
XXE, JSON injection) are only visible when the host logs bodies or WAF
matches; the report says so instead of implying full coverage.
"""
from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import parse_qsl, unquote_plus

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

SIGNATURES = (
    ("sensitive_file_probe", "Sensitive file probe", "edge_cases", "medium", re.compile(
        r"/(?:\.env(?:\.\w+)?$|\.git(?:/|$)|\.svn/|\.hg/|\.ds_store$|\.htpasswd$|\.htaccess$|\.npmrc$|\.aws/|\.ssh/|"
        r"id_rsa|wp-config\.php|config\.(?:php|json|ya?ml|bak)$|settings\.py$|database\.yml$|docker-compose\.ya?ml$|"
        r"[^/]+\.(?:bak|old|orig|swp|sql|sqlite|db|tar|tgz|zip|7z)$|phpinfo\.php$|server-status$|actuator(?:/|$)|"
        r"debug(?:/|$)|\.vscode/|composer\.(?:json|lock)$|package\.json$)", re.IGNORECASE)),
    ("path_traversal", "Path traversal / local file inclusion", "heat_mapping", "high", re.compile(
        r"(?:\.\./|\.\.\\|\.\.%2f|%2e%2e(?:%2f|/|\\)|/etc/passwd|/etc/shadow|win\.ini|boot\.ini|/proc/self/|"
        r"php://filter|file:///)", re.IGNORECASE)),
    ("sql_injection", "SQL injection attempt", "sqli", "high", re.compile(
        r"(?:union(?:\s|/\*.*?\*/)+(?:all(?:\s|/\*.*?\*/)+)?select|\bselect\b.{1,100}\bfrom\b|'\s*or\s*'?\d+'?\s*=\s*'?\d|"
        r"\bor\s+1\s*=\s*1\b|\b(?:sleep|pg_sleep)\s*\(\s*\d|benchmark\s*\(|waitfor\s+delay|information_schema|"
        r"xp_cmdshell|;\s*(?:drop|insert|update|delete)\s|'\s*--|\bextractvalue\s*\(|updatexml\s*\()", re.IGNORECASE)),
    ("xss", "Cross-site scripting attempt", "xss", "medium", re.compile(
        r"(?:<\s*script|<\s*svg[^>]*on\w+|<\s*img[^>]+on\w+\s*=|javascript\s*:|\bon(?:error|load|mouseover|focus)\s*=|"
        r"\balert\s*\(|\bprompt\s*\(|document\.cookie|<\s*iframe)", re.IGNORECASE)),
    ("command_injection", "Command injection attempt", "heat_mapping", "high", re.compile(
        r"(?:[;|`]|\$\(|&&|\|\|)\s*(?:cat|id|whoami|uname|ls|wget|curl|nc|ncat|bash|sh|ping|nslookup|powershell)\b", re.IGNORECASE)),
    ("template_injection", "Template / expression injection (incl. Log4Shell)", "web_fuzzing", "high", re.compile(
        r"(?:\{\{\s*\d+\s*\*\s*\d+\s*\}\}|\$\{\s*\d+\s*\*\s*\d+\s*\}|\$\{jndi:|<%=|#\{\s*\d+\s*\*)", re.IGNORECASE)),
)
SCANNER_UA = re.compile(
    r"(sqlmap|nikto|nuclei|ffuf|gobuster|dirbuster|\bdirb\b|wfuzz|feroxbuster|masscan|zgrab|nmap|wpscan|acunetix|"
    r"nessus|openvas|netsparker|burp\s?suite|arachni|whatweb|hydra|jaeles|dalfox|xsstrike|commix|katana|"
    r"projectdiscovery|interactsh)", re.IGNORECASE)
URL_PARAMS = re.compile(r"^(?:url|uri|u|link|src|source|dest|destination|redirect|redirect_uri|redirect_url|next|return|"
                        r"returnto|return_to|continue|callback|webhook|target|path|file|feed|image|img|proxy|fetch|load|"
                        r"domain|host|site|page|data|reference)$", re.IGNORECASE)
INTERNAL_HOST = re.compile(r"^(?:localhost|metadata\.google\.internal|metadata|instance-data|0\.0\.0\.0|\[::1?\])$", re.IGNORECASE)
LOGIN_PATH = re.compile(r"/(?:log-?in|sign-?in|auth(?:enticate)?|session|token|oauth/token|password|reset|otp|verify|"
                        r"2fa|mfa|admin/login|wp-login\.php|xmlrpc\.php)(?:/|$|\.)", re.IGNORECASE)
ID_SEGMENT = re.compile(r"^(?:\d{1,12}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24})$", re.IGNORECASE)

GUIDANCE = {
    "sensitive_file_probe": "Return 404 for dotfiles, backups and build artefacts; keep secrets out of the web root and "
                            "deployment output; rotate any secret that was ever served.",
    "path_traversal": "Never build file paths from request input; map identifiers to an allowlist and resolve the final path "
                      "inside a fixed base directory before opening it.",
    "sql_injection": "Use parameterised queries or the ORM's bound parameters on every route; never concatenate request "
                     "values into SQL; validate types (ids are integers/UUIDs).",
    "xss": "Rely on the framework's output encoding, avoid raw HTML sinks (innerHTML, dangerouslySetInnerHTML, |safe), "
           "and add a strict Content-Security-Policy.",
    "command_injection": "Do not pass request input to a shell; call programs with an argument list and an allowlist of values.",
    "template_injection": "Never render user input as a template; patch logging libraries (Log4Shell) and disable lookups.",
    "ssrf_attempt": "Fetch only allowlisted hosts; resolve and reject private, loopback and link-local addresses (including "
                    "169.254.169.254) after DNS resolution; disable redirects or re-check each hop.",
    "open_redirect": "Redirect only to relative paths or an allowlist of your own hosts.",
    "scanner": "Automated scanners are usually the first step of an attack; rate-limit or challenge them at the edge.",
    "content_discovery": "Return uniform 404s, remove unused routes, rate-limit 404-heavy clients, and make sure no admin or "
                         "debug route is reachable without authentication.",
    "fuzzing": "Validate input types and lengths before processing; return generic errors; rate-limit clients that cause errors.",
    "server_errors": "Find the code path behind each 5xx; unhandled errors under fuzzing often mean missing validation.",
    "login_bruteforce": "Rate-limit and lock out per account and per IP, add MFA, and use breached-password checks.",
    "idor_enumeration": "Check object ownership on every route that takes an id (the authenticated user must own or be "
                        "allowed the object); prefer unguessable ids, but never rely on them for access control.",
    "request_flood": "Apply per-IP rate limits and edge DDoS protection; cache public responses.",
    "host_probing": "Reject unknown Host headers at the edge; serve only your configured hostnames.",
}
TITLES = {
    "ssrf_attempt": ("Server-side request forgery attempt", "ssrf", "high"),
    "open_redirect": ("Open redirect attempt", "heat_mapping", "low"),
    "scanner": ("Known attack scanner", "vuln_automation", "low"),
    "content_discovery": ("Content discovery / directory brute force", "content_discovery", "medium"),
    "fuzzing": ("Parameter fuzzing burst", "web_fuzzing", "medium"),
    "server_errors": ("Errors triggered by one client", "web_fuzzing", "medium"),
    "login_bruteforce": ("Login brute force / credential stuffing", "big_questions", "high"),
    "idor_enumeration": ("Sequential object id enumeration (IDOR probing)", "idor", "high"),
    "request_flood": ("Request flood from one client", "recon_frameworks", "medium"),
    "host_probing": ("Host header / virtual host probing", "edge_cases", "low"),
}
for _technique, _title, _section, _severity, _ in SIGNATURES:
    TITLES[_technique] = (_title, _section, _severity)

WINDOW_SECONDS = 600
THRESHOLDS = {"content_discovery": 30, "fuzzing": 25, "server_errors": 10, "login_bruteforce": 10,
              "idor_enumeration": 15, "request_flood": 600, "host_probing": 8}


def _decoded(event: dict) -> str:
    raw = event["path"] + ("?" + event["query"] if event.get("query") else "")
    once = unquote_plus(raw)
    return unquote_plus(once) if "%" in once else once


def _internal_target(value: str) -> bool:
    value = value.strip()
    if not re.match(r"^(?:https?|gopher|dict|file|ftp|ldap)://", value, re.IGNORECASE):
        return False
    if value.lower().startswith(("file://", "gopher://", "dict://", "ldap://")):
        return True
    host = re.sub(r"^[a-z]+://", "", value, flags=re.IGNORECASE).split("/", 1)[0].split("@")[-1].rsplit(":", 1)[0].strip("[]")
    if INTERNAL_HOST.match(host):
        return True
    try:
        address = ipaddress.ip_address(int(host)) if host.isdigit() else ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified


def signatures(event: dict) -> list[str]:
    hits = []
    text = _decoded(event)
    for technique, _title, _section, _severity, pattern in SIGNATURES:
        if pattern.search(text):
            hits.append(technique)
    for name, value in parse_qsl(event.get("query") or "", keep_blank_values=True):
        if URL_PARAMS.match(name):
            if _internal_target(value):
                hits.append("ssrf_attempt")
            elif re.match(r"^(?:https?:)?//", value.strip(), re.IGNORECASE) and name.lower() in {
                    "redirect", "redirect_uri", "redirect_url", "next", "return", "returnto", "return_to", "continue"}:
                hits.append("open_redirect")
    if SCANNER_UA.search(event.get("ua") or ""):
        hits.append("scanner")
    return list(dict.fromkeys(hits))


def _epoch(event: dict) -> float | None:
    try:
        return datetime.fromisoformat(event["time"]).timestamp() if event.get("time") else None
    except ValueError:
        return None


def route_template(path: str) -> tuple[str, str | None]:
    parts = path.split("/")
    ids = [p for p in parts if ID_SEGMENT.match(p)]
    template = "/".join("{id}" if ID_SEGMENT.match(p) else p for p in parts)
    return template, (ids[-1] if ids else None)


def _successful(event: dict) -> bool:
    status = event.get("status") or 0
    return 200 <= status < 300 and (event.get("size") or 0) > 0 and event.get("waf_action") not in {"block", "drop", "challenge",
                                                                                                    "managed_challenge", "deny"}


def behaviours(events: list[dict]) -> list[tuple[str, str, list[dict], dict]]:
    """(technique, ip, events, detail) for per-client patterns inside WINDOW_SECONDS windows."""
    by_ip: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_ip[event["ip"]].append(event)
    found = []
    for ip, rows in by_ip.items():
        rows.sort(key=lambda e: _epoch(e) or 0)
        windows: list[list[dict]] = []
        for event in rows:
            stamp = _epoch(event)
            if windows and (stamp is None or windows[-1][0].get("_t") is None or stamp - windows[-1][0]["_t"] <= WINDOW_SECONDS):
                windows[-1].append(event)
            else:
                windows.append([event])
            event["_t"] = stamp
        for window in windows:
            missing = [e for e in window if e.get("status") == 404]
            if len(missing) >= THRESHOLDS["content_discovery"]:
                found.append(("content_discovery", ip, missing, {"not_found": len(missing),
                              "distinct_paths": len({e["path"] for e in missing})}))
            per_path = defaultdict(set)
            for e in window:
                if e.get("query"):
                    per_path[e["path"]].add(e["query"])
            fuzzed = {p: q for p, q in per_path.items() if len(q) >= THRESHOLDS["fuzzing"]}
            if fuzzed:
                path = max(fuzzed, key=lambda p: len(fuzzed[p]))
                found.append(("fuzzing", ip, [e for e in window if e["path"] == path],
                              {"path": path, "distinct_queries": len(fuzzed[path])}))
            errors = [e for e in window if (e.get("status") or 0) >= 500]
            if len(errors) >= THRESHOLDS["server_errors"]:
                found.append(("server_errors", ip, errors, {"errors": len(errors)}))
            logins = [e for e in window if e["method"] == "POST" and LOGIN_PATH.search(e["path"])]
            if len(logins) >= THRESHOLDS["login_bruteforce"]:
                found.append(("login_bruteforce", ip, logins, {"attempts": len(logins),
                              "statuses": dict(Counter(e.get("status") for e in logins))}))
            ids = defaultdict(set)
            for e in window:
                template, ident = route_template(e["path"])
                if ident is not None:
                    ids[template].add(ident)
            enumerated = {t: v for t, v in ids.items() if len(v) >= THRESHOLDS["idor_enumeration"]}
            if enumerated:
                template = max(enumerated, key=lambda t: len(enumerated[t]))
                hits = [e for e in window if route_template(e["path"])[0] == template]
                found.append(("idor_enumeration", ip, hits, {"route": template, "distinct_ids": len(enumerated[template]),
                              "successful": sum(1 for e in hits if _successful(e))}))
            if len(window) >= THRESHOLDS["request_flood"]:
                found.append(("request_flood", ip, window, {"requests": len(window)}))
            hosts = {e.get("host") for e in window if e.get("host")}
            if len(hosts) >= THRESHOLDS["host_probing"]:
                found.append(("host_probing", ip, window, {"distinct_hosts": len(hosts)}))
    for rows in by_ip.values():
        for event in rows:
            event.pop("_t", None)
    return found


def block_rules(technique: str, ip: str, paths: list[str]) -> dict:
    rules = {"nginx": f"deny {ip};", "cloudflare": f"ip.src eq {ip}",
             "note": "IP blocks stop one source; fix the route so the next attacker fails too."}
    if technique == "sensitive_file_probe":
        rules["nginx_route"] = "location ~ /\\.(?!well-known) { deny all; }"
        rules["cloudflare_route"] = ('http.request.uri.path contains "/." and not '
                                     'starts_with(http.request.uri.path, "/.well-known/")')
    elif technique == "login_bruteforce" and paths:
        rules["cloudflare_route"] = f'http.request.method eq "POST" and http.request.uri.path eq "{paths[0]}"'
        rules["note"] = "Use a rate-limiting rule on this expression rather than a permanent block."
    return rules


def sample(event: dict) -> str:
    from ..security import redact_text
    line = f"{event.get('time') or '-'} {event['method']} {event['path']}" + (f"?{event['query']}" if event.get("query") else "")
    line += f" -> {event.get('status')}" + (f" waf={event['waf_action']}" if event.get("waf_action") else "")
    return redact_text(line)[:600]
