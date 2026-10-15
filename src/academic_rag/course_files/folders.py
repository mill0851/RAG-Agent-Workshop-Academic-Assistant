"""The course folders on disk: the hand-off between the Canvas scraper and the index.

    data/courses/Intro to Machine Learning (93756976)/
        course.json  facts.json  home.md  syllabus.md
        announcements/  modules/  pages/  assignments/  discussions/  quizzes/
        files/<Canvas folder path>/lecture1.pdf        (original download)
        files/<Canvas folder path>/lecture1.pdf.md     (extracted text, what gets embedded)
        uploads/my-notes.pdf  uploads/my-notes.pdf.md  (files you added through the API)

This file has the folder layout and safe file writing. items.py reads and writes the
.md items; uploads.py adds your own files; courses.py answers questions about courses.

    safe_name(), slug()        names that work as files on every computer
    write_bytes(), write_json()  atomic writes: a crash never leaves half a file
    CourseStore                the course folders: find, create, rename, list
    prune_tab()                delete the items of one tab that are gone from Canvas
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # characters a Windows file name cannot hold


# ------------------------------------------------------------- names and io
def safe_name(s: str | None, max_len: int = 80, default: str = "untitled") -> str:
    """Make a string safe as a single file or folder name on Windows, macOS and Linux."""
    s = _ILLEGAL.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")
    return s or default


def slug(s: str | None, max_len: int = 50) -> str:
    """A short lowercase name for a file: "Week 3: Q&A" becomes "week-3-q-a"."""
    return re.sub(r"[^\w]+", "-", (s or "").lower()).strip("-")[:max_len].strip("-") or "item"


def write_bytes(path: Path, data: bytes) -> None:
    """Write a file, creating its folders: first to a .tmp file, then renamed into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file


def read_json(path: Path) -> dict | None:
    """A JSON file's contents, or None if it is missing or not valid JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict) -> None:
    """Write a JSON file, indented so a person can read it, safely (write_bytes)."""
    write_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


# -------------------------------------------------------------- course dirs
class CourseStore:
    """The course folders under data/courses/, each named "<course name> (<course id>)"."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def dirname(name: str, course_id: str) -> str:
        """The folder name for a course: "Physics I Mechanics (41001)"."""
        return f"{safe_name(name, 80, 'Course')} ({course_id})"

    def find(self, course_id: str) -> Path | None:
        """The folder of a course, whatever its name, by the id at the end; or None."""
        if not self.root.is_dir():
            return None
        suffix = f"({course_id})"
        for d in self.root.iterdir():
            if d.is_dir() and d.name.endswith(suffix) and d.name[: -len(suffix)].endswith(" "):
                return d
        return None

    def open_course(self, course_id: str, name: str) -> Path:
        """Create (or rename, if the course was renamed in Canvas) the course folder."""
        target = self.root / self.dirname(name, course_id)
        existing = self.find(course_id)
        if existing is not None and existing.name != target.name:
            existing.rename(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def course_dirs(self) -> list[Path]:
        """Every saved course: the folders that hold a course.json, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(d for d in self.root.iterdir() if d.is_dir() and (d / "course.json").exists())


# ------------------------------------------------------------------ pruning
def prune_tab(course_dir: Path, tab_id: str, keep: set[str]) -> int:
    """Delete files of one scraped tab that were not written/kept this run
    (i.e. items deleted in Canvas). `keep` holds paths relative to course_dir."""
    if tab_id in {"home", "syllabus"}:  # these two are single files, not folders
        candidates = [course_dir / f"{tab_id}.md"]
    else:
        base = course_dir / tab_id
        candidates = [p for p in base.rglob("*") if p.is_file()] if base.is_dir() else []

    removed = 0
    for p in candidates:
        if p.exists() and p.relative_to(course_dir).as_posix() not in keep:
            p.unlink()
            removed += 1

    base = course_dir / tab_id
    if base.is_dir():  # tidy now-empty folders (including the tab folder itself), deepest first
        folders = [d for d in base.rglob("*") if d.is_dir()] + [base]
        for d in sorted(folders, key=lambda d: len(d.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
    return removed
