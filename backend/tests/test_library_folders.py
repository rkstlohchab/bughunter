"""Library folders: origin, platform, author/course and section for every source."""
from __future__ import annotations

from pathlib import Path

import pytest

from bughunter.app import Application
from bughunter.library_folders import annotate, classify, tree

HOME = Path.home()


def src(ident, **fields):
    return {"id": ident, "title": fields.pop("title", ident), **fields}


def test_offline_course_folders_split_author_and_sections():
    haddix = classify(src("a", path=str(HOME / "Downloads/Jason Haddix - Bug Hunters Methodology 2024/Day 1 Recon .mp4")))
    assert (haddix["origin"], haddix["author"], haddix["course"], haddix["section"]) == \
        ("offline", "Jason Haddix", "Bug Hunters Methodology 2024", None)
    video = classify(src("b", path=str(HOME / "Downloads/game hacking course/videos/Video 1.mp4")))
    assert (video["author"], video["course"], video["section"]) == (None, "game hacking course", "videos")
    nested = classify(src("c", path=str(HOME / "Documents/Web Course/module 2/labs/a.pdf")))
    assert (nested["course"], nested["section"]) == ("Web Course", "module 2/labs")
    loose = classify(src("d", path=str(HOME / "Desktop/notes.pdf")))
    assert loose["course"] == "Individual files"
    volume = classify(src("e", path="/Volumes/Backup/OSCP Prep/day 3/slides.pdf"))
    assert (volume["course"], volume["section"]) == ("OSCP Prep", "day 3")
    icloud = classify(src("f", path=str(HOME / "Library/Mobile Documents/com~apple~CloudDocs/Burp Course/intro.mp4")))
    assert icloud["course"] == "Burp Course"
    stored = classify(src("g", path=str(HOME / "Library/Application Support/BugHunterClaudex/sources/abc.md")))
    assert stored["course"] == "Uploaded files" and stored["derived_from"] == "upload"
    rooted = classify(src("h", path="/tmp/import/Jane Doe - API Hacking/part1/a.mp4", import_root="/tmp/import/Jane Doe - API Hacking"))
    assert (rooted["author"], rooted["course"], rooted["section"]) == ("Jane Doe", "API Hacking", "part1")


def test_online_and_built_in():
    single = classify(src("y1", url="https://www.youtube.com/watch?v=abcdefghijk", video_id="abcdefghijk"))
    assert (single["origin"], single["platform"], single["course"]) == ("online", "YouTube", "Individual videos")
    channel = classify(src("y2", url="https://www.youtube.com/watch?v=abcdefghijk", video_id="abcdefghijk",
                           collection_title="NahamSec"))
    assert channel["course"] == "NahamSec"
    site = classify(src("w", url="https://www.portswigger.net/research/x", web_resource=True))
    assert (site["platform"], site["course"]) == ("Websites", "portswigger.net")
    built = classify(src("r", reference_pack=True, reference_kind="authored_synthesis", path=str(HOME / "Library/x.md")))
    assert (built["origin"], built["course"], built["section"]) == ("built_in", "BugHunter reference pack", None)


def test_user_override_wins_and_uploads_join_matching_course():
    moved = classify(src("m", path=str(HOME / "Downloads/misc/a.pdf"), library_course="Bug Hunters Methodology",
                         library_author="Jason Haddix"))
    assert (moved["author"], moved["course"], moved["derived_from"]) == ("Jason Haddix", "Bug Hunters Methodology", "user")
    rows = annotate([
        src("v", title="Video 1", path=str(HOME / "Downloads/game hacking course/videos/Video 1.mp4")),
        src("u", title="Ultimate_Game_Hacking_Course_Method_Map", path=str(HOME / "Library/Application Support/X/sources/1.md")),
        src("o", title="random notes", path=str(HOME / "Library/Application Support/X/sources/2.md")),
    ])
    folders = {r["id"]: r["library_folder"] for r in rows}
    assert (folders["u"]["course"], folders["u"]["section"]) == ("game hacking course", "Uploaded notes")
    assert folders["o"]["course"] == "Uploaded files"
    origins = tree([src("v", path=str(HOME / "Downloads/game hacking course/videos/Video 1.mp4")),
                    src("r", reference_pack=True), src("y", url="https://youtu.be/abcdefghijk", video_id="abcdefghijk")])
    assert [o["origin"] for o in origins] == ["offline", "online", "built_in"]
    assert origins[1]["folders"][0]["label"] == "YouTube › Individual videos"


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path / "data", primary=False)
    monkeypatch.setattr(a.operator, "_loop", lambda *args: None)
    yield a
    a.close()


def test_service_records_import_root_lists_folders_and_organizes(app, tmp_path):
    course = tmp_path / "Jason Haddix - Bug Hunters Methodology"
    (course / "slides").mkdir(parents=True)
    (course / "slides" / "day1.md").write_text("# Recon\nASNs and subdomains.")
    (course / "notes.md").write_text("# Notes\nHeat mapping.")
    batch = app.dispatch("sources.import", {"path": str(course)})
    listed = {s["id"]: s for s in app.dispatch("sources.list", {})}
    for source in batch["sources"]:
        folder = listed[source["id"]]["library_folder"]
        assert (folder["author"], folder["course"]) == ("Jason Haddix", "Bug Hunters Methodology")
        assert listed[source["id"]]["import_root"] == str(course)
    sections = {listed[s["id"]]["library_folder"]["section"] for s in batch["sources"]}
    assert sections == {None, "slides"}
    folders = app.dispatch("sources.folders", {})
    assert folders[0]["folders"][0]["label"] == "Jason Haddix › Bug Hunters Methodology"
    ids = [s["id"] for s in batch["sources"]]
    moved = app.dispatch("sources.organize", {"ids": ids[:1], "course": "TBHM", "author": "Jason Haddix"})
    assert moved["sources"][0]["library_folder"]["label"] == "Jason Haddix › TBHM"
    reset = app.dispatch("sources.organize", {"ids": ids[:1], "course": None, "author": None})
    assert reset["sources"][0]["library_folder"]["course"] == "Bug Hunters Methodology"
    with pytest.raises(ValueError):
        app.dispatch("sources.organize", {"ids": ["missing"], "course": "x"})
    found = app.library.sources("Jason Haddix course")["items"]
    assert found and all("Jason Haddix" in item["folder"] for item in found)
