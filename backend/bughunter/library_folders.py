"""Library folders: where a resource came from, then who made it or which course it belongs to.

Folder levels:

1. Origin: ``offline`` (files and folders the user provided), ``online`` (links:
   YouTube, websites) or ``built_in`` (the app's own reference pack).
2. Platform (online only): YouTube, or Websites.
3. Author and course, from the user's override, the import folder, a
   "Author - Course" folder name, the YouTube channel/playlist, or the site.
4. Section: the subfolder inside a course (for example ``videos``), if any.

Nothing is moved on disk; the folder is metadata computed from each source.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN_LABELS = {"offline": "Offline: your files and courses", "online": "Online: links you added",
                 "built_in": "Built-in: BugHunter reference notes"}
ORIGIN_ORDER = {"offline": 0, "online": 1, "built_in": 2}
CONTAINERS = ("Downloads", "Desktop", "Documents", "Movies", "Music", "Pictures", "Courses",
              "Library/Mobile Documents/com~apple~CloudDocs")
AUTHOR_SPLIT = re.compile(r"^\s*([A-Z][\w.'’]*(?:\s+[A-Z][\w.'’]*){0,3})\s+[-–—]\s+(.+?)\s*$")
_WORD = re.compile(r"[a-z0-9]+")
_GENERIC = {"the", "a", "an", "of", "and", "for", "to", "in", "course", "courses", "files", "file", "video", "videos",
            "part", "lesson", "lessons", "module", "day", "live", "master", "notes"}
LOOSE = "Individual files"


def _words(text: str) -> set[str]:
    return set(_WORD.findall(str(text or "").lower().replace("_", " ")))


def _split_author(name: str) -> tuple[str | None, str]:
    match = AUTHOR_SPLIT.match(name or "")
    if match and len(match[2]) >= 3:
        return match[1].strip(), match[2].strip()
    return None, (name or "").strip()


def _relative_parts(path: Path, app_root: str | None) -> tuple[list[str] | None, bool]:
    """Parts below the first user container (Downloads, Desktop, a volume ...); flag app-stored uploads."""
    text = str(path)
    if app_root and text.startswith(str(app_root).rstrip("/") + "/"):
        return None, True
    home = Path.home()
    for container in CONTAINERS:
        base = home / container
        try:
            return list(path.relative_to(base).parts), False
        except ValueError:
            continue
    if path.parts[:2] == ("/", "Volumes") and len(path.parts) > 3:
        return list(path.parts[3:]), False
    try:
        inner = list(path.relative_to(home).parts)
        # ~/Library holds app data (including this app's stored uploads), never a user's course folder.
        return (None, True) if inner[:1] == ["Library"] else (inner, False)
    except ValueError:
        return [path.parent.name, path.name] if path.parent.name else [path.name], False


def _online(source: dict) -> dict:
    url = str(source.get("url") or "")
    if source.get("video_id") or str(source.get("media_type") or "") == "video/youtube" or "youtu" in url:
        author = source.get("library_author") or source.get("channel") or source.get("collection_title") or "Individual videos"
        return {"origin": "online", "platform": "YouTube", "author": None, "course": author}
    host = (urlsplit(url).hostname or "unknown site").lower().removeprefix("www.")
    return {"origin": "online", "platform": "Websites", "author": None, "course": host}


def classify(source: dict, app_root: str | None = None) -> dict:
    if source.get("reference_pack"):
        folder = {"origin": "built_in", "platform": None, "author": None, "course": "BugHunter reference pack",
                  "section": None, "derived_from": "reference_pack"}
    elif source.get("url"):
        folder = {**_online(source), "section": None, "derived_from": "link"}
    else:
        path = Path(str(source.get("path") or ""))
        root = source.get("import_root")
        author, course, section, derived = None, LOOSE, None, "path"
        if root:
            base = Path(root)
            author, course = _split_author(base.name)
            if source.get("import_relpath") is not None:
                inner = [p for p in str(source["import_relpath"]).split("/") if p]
            else:
                try:
                    inner = list(path.relative_to(base).parts[:-1])
                except ValueError:
                    inner = []
            section, derived = "/".join(inner) or None, "import_folder"
        elif source.get("path"):
            parts, uploaded = _relative_parts(path, app_root)
            if uploaded:
                course, derived = "Uploaded files", "upload"
            elif parts and len(parts) >= 2:
                author, course = _split_author(parts[0])
                section = "/".join(parts[1:-1]) or None
        else:
            course, derived = "Uploaded files", "upload"
        folder = {"origin": "offline", "platform": None, "author": author, "course": course, "section": section,
                  "derived_from": derived}
    if source.get("library_course"):
        folder.update(course=str(source["library_course"]), author=source.get("library_author") or None,
                      section=None, derived_from="user")
    elif source.get("library_author") and folder["origin"] != "online":
        folder.update(author=str(source["library_author"]), derived_from="user")
    return folder


def _attach_uploads(folders: dict[str, dict], titles: dict[str, str]) -> None:
    """An uploaded note named after a known course joins that course (e.g. a course's method map)."""
    courses = {}
    for folder in folders.values():
        if folder["origin"] == "offline" and folder["derived_from"] in {"path", "import_folder"} and folder["course"] != LOOSE:
            key = (folder["author"], folder["course"])
            courses[key] = _words(folder["course"]) - _GENERIC
    for ident, folder in folders.items():
        if folder["derived_from"] != "upload":
            continue
        words = _words(titles.get(ident, ""))
        best = max(((len(w), key) for key, w in courses.items() if len(w) >= 1 and w <= words and len(words & w) >= 1),
                   default=None)
        if best:
            folder.update(author=best[1][0], course=best[1][1], section="Uploaded notes", derived_from="matched_upload")


def folder_label(folder: dict) -> str:
    parts = [p for p in (folder.get("platform"), folder.get("author"), folder.get("course")) if p]
    return " › ".join(parts)


def annotate(sources: list[dict], app_root: str | None = None) -> list[dict]:
    folders = {s["id"]: classify(s, app_root) for s in sources}
    _attach_uploads(folders, {s["id"]: str(s.get("title") or s.get("filename") or "") for s in sources})
    out = []
    for source in sources:
        folder = folders[source["id"]]
        folder["origin_label"] = ORIGIN_LABELS[folder["origin"]]
        folder["label"] = folder_label(folder)
        out.append({**source, "library_folder": folder})
    return out


def tree(sources: list[dict], app_root: str | None = None) -> list[dict]:
    """Origin -> folder -> section, with source ids, for clients and agents."""
    origins: dict[str, dict] = {}
    for source in annotate(sources, app_root):
        folder = source["library_folder"]
        origin = origins.setdefault(folder["origin"], {"origin": folder["origin"], "label": folder["origin_label"],
                                                       "count": 0, "folders": {}})
        group = origin["folders"].setdefault(folder["label"], {"label": folder["label"], "platform": folder["platform"],
                                                               "author": folder["author"], "course": folder["course"],
                                                               "count": 0, "sections": {}})
        group["sections"].setdefault(folder["section"] or "", []).append(source["id"])
        group["count"] += 1
        origin["count"] += 1
    result = []
    for key in sorted(origins, key=lambda k: ORIGIN_ORDER[k]):
        origin = origins[key]
        origin["folders"] = [
            {**group, "sections": [{"name": name or None, "source_ids": ids} for name, ids in sorted(group["sections"].items())]}
            for _, group in sorted(origin["folders"].items(), key=lambda item: (item[1]["course"] in (LOOSE, "Uploaded files"), item[0].lower()))]
        result.append(origin)
    return result
