"""Questions about the saved courses: which exist, their details, their exact facts and
their items. The API, the CLI and the agent all ask through these functions.

    list_courses()     every saved course, with its summary and index counts
    course_dir()       the folder of one course
    get_course()       one course's details, from its course.json
    get_facts()        one course's facts.json: due dates, points, scores, grade
    list_documents()   one course's saved items, read from their headers
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .folders import CourseStore, read_json
from .items import read_meta


def _index_counts(settings: Settings) -> dict[str, dict]:
    """Docs and chunks per course, from the embed manifest (no need to open the index)."""
    counts: dict[str, dict] = {}
    for m in (read_json(settings.manifest_path) or {}).values():
        c = counts.setdefault(m["course_id"], {"docs": 0, "chunks": 0})
        c["docs"] += 1
        c["chunks"] += m.get("chunks", 0)
    return counts


def _course_entry(meta: dict, counts: dict[str, dict]) -> dict:
    """One course as the API returns it: the details in its course.json, and how much of it
    is in the index."""
    idx = counts.get(str(meta["id"]), {"docs": 0, "chunks": 0})
    return {
        "id": str(meta["id"]), "name": meta.get("name"), "name_source": meta.get("name_source"),
        "course_code": meta.get("course_code"), "term": meta.get("term"),
        "summary": meta.get("summary", ""), "summary_source": meta.get("summary_source", "none"),
        "folder": meta.get("folder"), "canvas_url": meta.get("canvas_url"),
        "counts": meta.get("counts", {}), "tabs": meta.get("tabs", []),
        "scraped_at": meta.get("scraped_at"),
        "indexed_docs": idx["docs"], "indexed_chunks": idx["chunks"],
    }


def list_courses(settings: Settings) -> list[dict]:
    """Every saved course (a folder with a course.json), sorted by name."""
    counts = _index_counts(settings)
    entries = [_course_entry(read_json(d / "course.json") or {"id": d.name}, counts)
               for d in CourseStore(settings.courses_dir).course_dirs()]
    return sorted(entries, key=lambda c: (c["name"] or "").lower())


def course_dir(settings: Settings, course_id: str) -> Path | None:
    """The folder of one course, or None if it was never saved."""
    return CourseStore(settings.courses_dir).find(str(course_id))


def get_course(settings: Settings, course_id: str) -> dict | None:
    """One course's details, as list_courses() returns them, or None."""
    d = course_dir(settings, course_id)
    meta = read_json(d / "course.json") if d else None
    return _course_entry(meta, _index_counts(settings)) if meta else None


def get_facts(settings: Settings, course_id: str) -> dict | None:
    """One course's facts.json (due dates, points, scores, grade), or None."""
    d = course_dir(settings, course_id)
    return read_json(d / "facts.json") if d else None


def list_documents(settings: Settings, course_id: str, source_type: str | None = None) -> list[dict] | None:
    """One course's saved items (only one type, with `source_type`), read from each .md
    file's header. None if the course does not exist."""
    d = course_dir(settings, course_id)
    if d is None:
        return None

    out = []
    for p in sorted(d.rglob("*.md")):
        meta = read_meta(p)
        if not meta or "doc_id" not in meta or (source_type and meta.get("source_type") != source_type):
            continue  # not one of our items, or another type
        out.append({
            "doc_id": meta["doc_id"], "title": meta.get("title", ""), "type": meta.get("source_type", ""),
            "module": meta.get("module", ""), "url": meta.get("url", ""),
            "updated_at": meta.get("updated_at", ""), "path": p.relative_to(settings.data_dir).as_posix(),
            "has_text": not meta.get("no_text", False),
        })
    return out
