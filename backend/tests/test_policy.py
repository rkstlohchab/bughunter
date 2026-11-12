import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from bughunter.policy import PolicyService, canonical_url, classify_effect, normalized_request, validate_scope
from bughunter.store import Store


@pytest.fixture
def policy(tmp_path):
    service = PolicyService(Store(tmp_path))
    service.save_project({"id": "project", "name": "Testing", "scope": ["https://example.com/api"],
                          "authorization": "Authorized test project", "read_only_paths": ["/api/products"]})
    return service


def request(path="/api/cart", method="POST"):
    return {"url": "https://example.com"+path, "method": method, "headers": {"content-type": "application/json", "cookie": "session=alice"}, "body": '{"price":10}' if method == "POST" else ""}


def test_scope_exact_host_path_and_exclusions(policy):
    project = policy.project("project")
    assert validate_scope(project, "https://example.com/api/products", resolve=False) == []
    for target in ("https://example.com.evil/api", "https://example.com/apiv2", "http://example.com/api", "https://example.com:444/api"):
        with pytest.raises(ValueError): validate_scope(project, target, resolve=False)
    project["exclusions"] = ["https://example.com/api/private"]
    with pytest.raises(ValueError): validate_scope(project, "https://example.com/api/private/a", resolve=False)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:password@example.com/", "https://example.com/api/../admin", "https://example.com/api/%2e%2e/admin", "https://example.com/api%2fprivate", "https://example.com/api%252fprivate", "https://example.com/api%255cprivate", "https://example.com/api\\x", "https://example.com/\n"])
def test_ambiguous_urls_rejected(url):
    with pytest.raises(ValueError): canonical_url(url)


def test_private_dns_blocked_and_mixed_resolution_blocked(policy, monkeypatch):
    monkeypatch.setattr("bughunter.policy.socket.getaddrinfo", lambda *a,**kw: [(2,1,6,"",("93.184.216.34",443)),(2,1,6,"",("127.0.0.1",443))])
    with pytest.raises(ValueError, match="Private"): validate_scope(policy.project("project"), "https://example.com/api")


def test_percent_encoded_letters_do_not_bypass_exclusions(policy):
    project = policy.project("project")
    project["exclusions"] = ["https://example.com/api/admin"]
    with pytest.raises(ValueError, match="excluded"):
        validate_scope(project, "https://example.com/api/%61dmin", resolve=False)


def test_state_changing_get_and_graphql_not_classified_from_verb(policy):
    project = policy.project("project")
    assert classify_effect(project, request("/api/products", "GET"))[0] == "read_only"
    project["read_only_paths"].append("/api/delete")
    assert classify_effect(project, request("/api/delete", "GET"))[0] == "state_changing"
    assert classify_effect(project, request("/api/products?delete=1", "GET"))[0] == "state_changing"
    assert classify_effect(project, request("/api/unclassified", "GET"))[0] == "unknown"
    project["read_only_paths"].append({"path": "/api/graphql", "methods": ["POST"]})
    gql = request("/api/graphql"); gql["body"] = '{"query":"mutation { removeCart }"}'
    assert classify_effect(project, gql)[0] == "state_changing"


@pytest.mark.parametrize('method,path,body,content_type', [
    ('POST', '/api/graphql', r'{"query":"mu\u0074ation { changeCart }"}', 'application/json'),
    ('GET', '/api/graphql?query=mutation%20%7BchangeCart%7D', '', 'application/json'),
    ('GET', '/api/products?query=mutation%20%7BchangeCart%7D', '', 'application/json'),
    ('POST', '/api/graphql', '[{"query":"{ cart { id } }"},{"query":"mutation { changeCart }"}]', 'application/json'),
    ('POST', '/api/graphql', 'query=mutation+%7BchangeCart%7D', 'application/x-www-form-urlencoded'),
    ('POST', '/api/graphql', '{"query":"mutation { changeCart }","query":"{ cart { id } }"}', 'application/json'),
    ('POST', '/api/products', '{"operationName":"Cart","extensions":{"persistedQuery":{"sha256Hash":"abc"}}}', 'application/json'),
    ('GET', '/api/products?extensions=%7B%22persistedQuery%22%3A%7B%7D%7D', '', 'application/json'),
    ('POST', '/api/graphql', '{"query":', 'application/json'),
    ('POST', '/api/graphql', '{"query":"query Cart { cart { id } }"}', 'application/json'),
    ('POST', '/api/products', '{ cart { id } }', 'application/graphql'),
    ('POST', '/api/products', 'query Cart { cart { id } }', 'text/plain'),
    ('POST', '/api/products', '--boundary\r\noperations', 'multipart/form-data; boundary=boundary'),
])
def test_graphql_envelopes_never_inherit_path_level_read_only_permission(policy, method, path, body, content_type):
    project = policy.project('project')
    project['read_only_paths'] = [{'path': '/api/*', 'methods': ['GET', 'POST']}]
    policy.save_project(project)
    action = policy.queue('project', {'method': method, 'url': 'https://example.com' + path,
        'headers': {'content-type': content_type}, 'body': body})
    assert action['effect'] in {'unknown', 'state_changing'}
    assert action['status'] == 'pending_approval'
    with pytest.raises(ValueError): policy._claim(action['id'])


def test_single_use_approval_bound_to_request_and_identity(policy):
    action = policy.queue("project", request(), "alice")
    assert action["status"] == "pending_approval"
    with pytest.raises(ValueError): policy._claim(action["id"])
    policy.approve(action["id"])
    claimed, ticket = policy._claim(action["id"])
    assert claimed["approval"]["consumed"]
    assert policy.consume_ticket(ticket, claimed["request"])["id"] == action["id"]
    with pytest.raises(ValueError): policy.consume_ticket(ticket, claimed["request"])
    with pytest.raises(ValueError): policy._claim(action["id"])


def test_final_gateway_request_change_does_not_consume_ticket(policy):
    action = policy.queue("project", request(), "alice")
    policy.approve(action["id"])
    action, ticket = policy._claim(action["id"])
    changed = copy.deepcopy(action["request"]); changed["headers"]["cookie"] = "session=bob"
    with pytest.raises(ValueError, match="changed"): policy.consume_ticket(ticket, changed)
    assert policy.consume_ticket(ticket, action["request"])["id"] == action["id"]


def test_concurrent_approval_claim_is_atomic(policy):
    action = policy.queue("project", request())
    policy.approve(action["id"])
    def claim():
        try: policy._claim(action["id"]); return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: claim(), range(2))) == 1


def test_mutated_action_and_scope_invalidate_approval(policy):
    action = policy.queue("project", request())
    policy.approve(action["id"])
    action = policy.store.get("action", action["id"])
    action["request"]["body"] = '{"price":1}'
    policy.store.put("action", action)
    with pytest.raises(ValueError, match="changed"): policy._claim(action["id"])
    second = policy.queue("project", request())
    policy.approve(second["id"])
    project = policy.project("project"); project["exclusions"] = ["https://example.com/api/cart"]
    policy.save_project(project)
    with pytest.raises(ValueError, match="scope changed"): policy._claim(second["id"])


def test_expired_and_denied_actions_cannot_run(policy):
    action = policy.queue("project", request()); policy.approve(action["id"])
    action = policy.store.get("action", action["id"])
    action["approval"]["expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    policy.store.put("action", action)
    with pytest.raises(ValueError, match="expired"): policy._claim(action["id"])
    another = policy.queue("project", request()); policy.deny(another["id"])
    with pytest.raises(ValueError): policy.approve(another["id"])


def test_stop_and_recovery_prevent_replay(policy):
    executing = policy.queue("project", request()); policy.approve(executing["id"]); policy._claim(executing["id"])
    approved = policy.queue("project", request()); policy.approve(approved["id"])
    policy.recover()
    assert policy.store.get("action", executing["id"])["status"] == "unknown"
    assert policy.store.get("action", approved["id"])["status"] == "cancelled"
    ready = policy.queue("project", request("/api/products", "GET"))
    policy.stop()
    with pytest.raises(ValueError): policy._claim(ready["id"])


def test_budget_and_concurrency_enforced(policy):
    project = policy.project("project"); project.update(max_requests=2, max_concurrency=1)
    policy.save_project(project)
    action = policy.queue("project", request("/api/products", "GET"))
    policy.reserve(action)
    with pytest.raises(ValueError, match="Concurrency"): policy.reserve(action)
    policy.release(action); policy.reserve(action); policy.release(action)
    with pytest.raises(ValueError, match="budget"): policy.reserve(action)


def test_live_transport_never_falls_back_to_direct_network(policy, monkeypatch):
    monkeypatch.setattr("bughunter.policy.socket.getaddrinfo", lambda *a,**kw: [(2,1,6,"",("93.184.216.34",443))])
    action = policy.queue("project", request("/api/products", "GET"))
    with pytest.raises(ValueError, match="routing"): policy.execute(action["id"])
    assert policy.store.get("action", action["id"])["status"] == "ready"


def test_injected_headers_rejected():
    for headers in ({"X-BugHunter-Ticket":"forged"}, {"x-test":"hello\r\nHost: evil"}, {"Upgrade":"websocket"}):
        with pytest.raises(ValueError): normalized_request({**request(), "headers":headers})
