"""TBHM methodology: source detection, cited retrieval, coverage, and app/agent wiring."""
from __future__ import annotations

import json

import pytest

from bughunter.app import Application
from bughunter.haddix_methodology import checklist
from bughunter.tbhm import SECTIONS, TbhmService, web_intent

DECK1 = "Bug Hunters Methodology Live Day One Recon "
DECK2 = "Bug Hunters Methodology Live Day Two App Analysis Master "


def _seed(store, title, chunks, *, project_id=None):
    source = store.put("source", {"title": title, "status": "ready", "project_id": project_id,
                                  "platform_scope": "shared", "chunk_count": len(chunks)})
    with store.connection(immediate=True) as db:
        for ordinal, (locator, text) in enumerate(chunks):
            ident = f"{source['id']}_c{ordinal}"
            db.execute("INSERT INTO knowledge_chunks(id,source_id,ordinal,text,original_text,locator,modality,metadata,corrected)"
                       " VALUES(?,?,?,?,?,?,?,?,0)", (ident, source["id"], ordinal, text, text, locator, "text",
                                                      json.dumps({"platform_scope": "shared"})))
            db.execute("INSERT INTO knowledge_fts(id,source_id,text,title,locator) VALUES(?,?,?,?,?)",
                       (ident, source["id"], text, title, locator))
    return source


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path, primary=False)
    monkeypatch.setattr(a.operator, "_loop", lambda *args: None)
    _seed(a.store, DECK1, [
        ("Page 7", "Green = site had no vulns. Orange = in progress. Red = vulns found."),
        ("Page 23", "ASNs++"),
        ("Page 23 · image interpretation", "### Observed on the page Section-divider slide with a decorative image."),
        ("Page 24", "Wide vs. Narrow Recon WHAT ARE WE AFTER? ASNs will give us IP addresses of owned servers."),
        ("Page 25", "Autonomous System Numbers are given to large enough networks; use BGP data to find them."),
        ("Page 31 · image interpretation", "### Observed on the page Shodan search results for the target organisation."),
    ])
    _seed(a.store, DECK2, [
        ("Page 59", "HEAT MAPPING"),
        ("Page 61", "HEAT MAPPING (UPLOADS) One of the more common places to see vulnerabilities is wherever files are uploaded."),
        ("Page 85", "IDOR (Autorize) Autorize remains one of the best plugins to find access control issues and IDORs."),
        ("Page 86", "IDOR (PARAMS) Parameters most likely to be subject to simple IDORs: id user account number order."),
    ])
    _seed(a.store, "Day 2 App Analysis hide01.ir", [
        ("Frame at 06:27:30", "The slide is titled IDOR Autorize and the speaker explains replaying requests with a second account."),
        ("Frame at 05:10:00", "Heat mapping uploads are a common place for bugs."),
    ])
    _seed(a.store, "Unrelated course on cooking", [("Page 1", "Recon your pantry before cooking.")])
    _seed(a.store, "commerce-pricing", [("section-1", "# Commerce pricing and discount validation. Establish the authoritative calculation; change one input at a time.")])
    _seed(a.store, "races-and-case-studies", [("section-1", "# Race conditions, workflow states. Apply one coupon concurrently and inspect the durable final state.")])
    _seed(a.store, "access-and-sessions", [("section-1", "# API access control. Two accounts, known ownership of carts and orders.")])
    _seed(a.store, "food-ordering", [("section-1", "# Food-ordering methods. Menu, add-ons, delivery and promotions are priced separately.")])
    a.store.put("project", {"name": "Shop", "scope": ["https://shop.example.test/"], "authorization": "Owned staging"}, id="proj_shop")
    yield a
    a.close()


def test_sources_detected_by_title_not_id(app):
    found = app.tbhm.sources()
    assert {k: v["title"] for k, v in found.items()} == {
        "deck:day1": DECK1, "deck:day2": DECK2, "transcript:day2": "Day 2 App Analysis hide01.ir"}


def test_section_cites_deck_pages_and_talk(app):
    asn = app.tbhm.section("asn")
    locators = [p["locator"] for p in asn["slide_evidence"]]
    assert locators[:2] == ["Page 24", "Page 25"]               # substantive slides first
    assert "Page 23 · image interpretation" not in locators     # decorative divider dropped
    assert all(p["source_id"] and p["title"] == DECK1 for p in asn["slide_evidence"])
    assert "Third-party data only" in asn["traffic_rule"] and asn["investigation_tools"] == ["web_search"]
    idor = app.tbhm.section("idor")
    assert [p["locator"] for p in idor["slide_evidence"]] == ["Page 85", "Page 86"]
    assert idor["talk_evidence"] and "06:27:30" in idor["talk_evidence"][0]["locator"]
    assert idor["traffic"] == "active" and "queue_action" in idor["investigation_tools"]
    with pytest.raises(ValueError):
        app.tbhm.section("made_up")


def test_missing_deck_degrades_to_outline(tmp_path, monkeypatch):
    a = Application(tmp_path / "empty", primary=False)
    try:
        section = a.tbhm.section("heat_mapping")
        assert section["slide_evidence"] == [] and "not found" in section["evidence_status"]
        assert section["checks"]
    finally:
        a.close()


def test_progress_uses_mind_map_legend_and_validates(app):
    assert app.tbhm.progress("proj_shop")["counts"]["todo"] == len(SECTIONS)
    with pytest.raises(ValueError, match="needs a note"):
        app.tbhm.record("proj_shop", "idor", "findings")
    with pytest.raises(ValueError):
        app.tbhm.record("proj_shop", "idor", "done")
    with pytest.raises(ValueError):
        app.tbhm.record("missing", "idor", "in_progress")
    state = app.tbhm.record("proj_shop", "idor", "findings", note="Order 1002 readable by account B", evidence_ids=["action_1"])
    row = next(r for r in state["sections"] if r["id"] == "idor")
    assert row["colour"] == "red" and row["evidence_ids"] == ["action_1"]
    assert "idor" not in state["next"]
    outline = app.tbhm.outline("proj_shop")
    assert any(s["id"] == "idor" and s["status"] == "findings" for p in outline["phases"] for s in p["sections"])


def test_web_intent_and_context(app):
    assert web_intent("Run bug bounty recon on shop.example.test")
    assert web_intent("check this web app for IDOR")
    assert not web_intent("summarize my day 1 course")
    assert app.tbhm.context("summarize my day 1 course") == ""
    context = app.tbhm.context("test the website for IDOR", "proj_shop")
    assert context.startswith("\nWEBSITE TESTING METHODOLOGY (TBHM")
    data = json.loads(context.rsplit("\n", 1)[1])
    assert data["focus_sections"][0]["id"] == "idor"
    assert len(context) < 9500


def test_harness_injects_tbhm_for_website_tasks_only(app):
    web = app.harness.start("Test the website shop.example.test for IDOR", grants=["app"], web_search_mode="off")
    run = app.store.get("operator_run", web["run_id"])
    assert "WEBSITE TESTING METHODOLOGY (TBHM" in run["transcript"][0]["content"]
    other = app.harness.start("Summarize my notes", grants=["app"], web_search_mode="off")
    assert "TBHM" not in app.store.get("operator_run", other["run_id"])["transcript"][0]["content"]


def test_task_tools_and_plan_mode(app):
    from bughunter.operator_tools import OperatorTools
    chat = app.store.put("chat", {"title": "Shop test", "project_id": "proj_shop", "messages": []})
    auto = app.harness.start("Test the website for XSS", grants=["app"], id=chat["id"], web_search_mode="off")
    tools = OperatorTools(app, auto["run_id"])
    names = {s["name"] for s in tools.specs()}
    assert {"tbhm_methodology", "tbhm_progress"} <= names
    assert tools.tbhm_methodology("xss")["id"] == "xss"
    recorded = tools.tbhm_progress("xss", "in_progress", note="Mapping reflections")
    assert next(r for r in recorded["sections"] if r["id"] == "xss")["colour"] == "orange"
    plan_chat = app.store.put("chat", {"title": "Plan", "project_id": "proj_shop", "messages": []})
    plan = app.harness.start("Test the website for XSS", grants=["app"], id=plan_chat["id"], execution_mode="plan",
                             web_search_mode="off")
    planner = OperatorTools(app, plan["run_id"])
    assert planner.tbhm_progress()["project_id"] == "proj_shop"
    with pytest.raises(PermissionError, match="Plan mode"):
        planner.tbhm_progress("xss", "no_findings")


def test_rpc_checklist_and_pentagi_plan_link_tbhm(app):
    assert app.dispatch("tbhm.section", {"section_id": "heat_mapping"})["pages"] == "59-68"
    stages = {s["id"]: s for s in checklist()}
    assert any(t["id"] == "idor" for t in stages["identity"]["tbhm_sections"])
    assert all(stage["reference"].startswith("https://") for stage in stages.values())
    prompt = app.pentagi.plan("proj_shop")["prompt"]
    assert "TBHM idor: IDOR and access control" in prompt and "tbhm_methodology section_id=" in prompt


def test_commerce_requests_attach_notes_and_cart_sections(app):
    context = app.tbhm.context("check my cart: change the price and quantity and apply a coupon at checkout", "proj_shop")
    assert context and "COMMERCE:" in context
    data = json.loads(context.rsplit("\n", 1)[1])
    titles = [n["title"] for n in data["commerce_notes"]]
    assert titles == ["commerce-pricing", "races-and-case-studies", "access-and-sessions"]
    assert all(n["chunk_ids"] and "not TBHM" in n["provenance"] for n in data["commerce_notes"])
    assert {"idor", "heat_mapping", "big_questions"} <= {s["id"] for s in data["focus_sections"]}
    food = json.loads(app.tbhm.context("test coupon and delivery fee on my food menu", "proj_shop").rsplit("\n", 1)[1])
    assert "food-ordering" in [n["title"] for n in food["commerce_notes"]]
    plain = json.loads(app.tbhm.context("test the website for xss", "proj_shop").rsplit("\n", 1)[1])
    assert "commerce_notes" not in plain
    bundle = app.tbhm.section("commerce_notes")
    assert len(bundle["notes"]) == 4 and bundle["tbhm_sections"] == ["idor", "heat_mapping", "big_questions"]
    assert "commerce_notes" in app.pentagi.plan("proj_shop")["prompt"]
