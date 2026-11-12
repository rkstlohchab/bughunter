"""Asset DB upserts, finds, relations."""
import pytest
from bughunter.app import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def test_upsert_host_dedupes_by_ip(app):
    h1 = app.assets.upsert_host(ip="10.0.0.5", hostname="box.lab", source="nmap")
    h2 = app.assets.upsert_host(ip="10.0.0.5", os_hint="linux", source="httpx")
    assert h1["id"] == h2["id"]
    assert h2["os_hint"] == "linux"
    assert h2["hostname"] == "box.lab"   # preserved


def test_service_dedupes_by_host_port_proto(app):
    host = app.assets.upsert_host(ip="10.0.0.5")
    s1 = app.assets.upsert_service(host_id=host["id"], port=22, proto="tcp",
                                    name="ssh")
    s2 = app.assets.upsert_service(host_id=host["id"], port=22, proto="tcp",
                                    banner="OpenSSH 9.2")
    assert s1["id"] == s2["id"]
    assert s2["banner"].startswith("OpenSSH")


def test_find_matches_blob(app):
    app.assets.upsert_host(ip="10.0.0.5", hostname="lab-01")
    app.assets.add_loot(kind="ssh-key", path="/tmp/key",
                        notes="exfil candidate")
    hits = app.assets.find(query="lab-01")
    assert len(hits["hosts"]) == 1
    hits2 = app.assets.find(query="candidate")
    assert len(hits2["loot"]) == 1


def test_graph_filters_to_project(app):
    app.assets.upsert_host(ip="1.2.3.4", project_id="p1")
    app.assets.upsert_host(ip="5.6.7.8", project_id="p2")
    g = app.assets.graph(project_id="p1")
    assert len(g["hosts"]) == 1
    assert g["hosts"][0]["ip"] == "1.2.3.4"
