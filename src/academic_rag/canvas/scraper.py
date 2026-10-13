"""Walk Canvas one course at a time and save every readable tab into that course's
folder (see course_files/ for the layout). Start reading at scrape().

Which tabs exist is decided by Canvas itself (/courses/:id/tabs), so a course
with Quizzes switched off or Pages hidden is handled without special cases.

    scraper.py   scrape() and the walk through one course
    tabs.py      one function per tab (pages, assignments, ...) and facts.json
    files.py     file downloads
    naming.py    a real name and a short summary for each course
    context.py   what one course's handlers share, and the numbers a scrape reports
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..course_files import CourseStore, html_to_text, prune_tab, read_json, write_json
from . import files, tabs
from .client import CanvasClient, CanvasNotAvailable
from .context import CourseContext, CourseResult, ScrapeStats, optional
from .naming import (  # noqa: F401  (re-exported: tests and callers import them from here)
    extractive_summary, looks_like_id, resolve_course_name, summary_inputs_hash, syllabus_title,
)

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]

HANDLERS = {
    "home": tabs.home, "syllabus": tabs.syllabus, "modules": tabs.modules, "pages": tabs.pages,
    "assignments": tabs.assignments, "quizzes": tabs.quizzes, "discussions": tabs.discussions,
    "announcements": tabs.announcements, "files": files.files,
}
HANDLER_ORDER = ["home", "syllabus", "modules", "pages", "assignments", "quizzes",
                 "discussions", "announcements", "files"]
SKIP_NOTES = {
    "grades": "grades are saved in facts.json",
    "people": "not course material",
    "outcomes": "not course material",
    "rubrics": "rubrics are included with their assignments",
    "chat": "not course material",
    "collaborations": "not readable through the Canvas API",
    "conferences": "not readable through the Canvas API",
}


# ------------------------------------------------------------- start here
def make_canvas(settings: Settings) -> CanvasClient:
    settings.require_canvas()
    return CanvasClient(settings.canvas_base_url, settings.canvas_api_token)


def scrape(
    settings: Settings,
    *,
    course_ids: list[str] | None = None,
    include_files: bool = True,
    prune: bool = True,
    progress: ProgressFn | None = None,
    client: CanvasClient | None = None,
) -> ScrapeStats:
    """Save every course (or just `course_ids`) into data/courses/."""
    scraper = CanvasScraper(client or make_canvas(settings), settings, CourseStore(settings.courses_dir),
                            course_ids=course_ids, include_files=include_files, prune=prune, progress=progress)
    return scraper.run()


class CanvasScraper:
    def __init__(self, client: CanvasClient, settings: Settings, store: CourseStore, *,
                 course_ids: list[str] | None = None, include_files: bool = True, prune: bool = True,
                 progress: ProgressFn | None = None):
        self.client = client
        self.settings = settings
        self.store = store
        self.course_ids = [str(c) for c in course_ids or settings.course_ids]
        self.include_files = include_files
        self.prune = prune
        self.progress = progress or (lambda msg, done, total: None)
        self.base = settings.canvas_base_url

    # --- courses
    def select_courses(self) -> list[dict]:
        wanted = list(dict.fromkeys(self.course_ids))
        if not wanted:
            return self.client.list_courses(self.settings.enrollment_states)
        courses = []
        for cid in wanted:
            c = optional(lambda cid=cid: self.client.get_course(cid), f"course {cid}", None)
            if c and c.get("name"):
                courses.append(c)
            else:
                log.warning("course %s was not found or is not accessible with this token", cid)
        return courses

    def run(self) -> ScrapeStats:
        stats = ScrapeStats()
        courses = self.select_courses()  # CanvasAuthError propagates to the caller
        log.info("found %d course(s)", len(courses))
        for i, course in enumerate(courses):
            self.progress(f"Scraping {course.get('name')}", i, len(courses))
            stats.courses.append(self.scrape_course(course))
        self.progress("Scrape finished", len(courses), len(courses))
        return stats

    # --- one course
    def scrape_course(self, course: dict) -> CourseResult:
        cid = str(course["id"])
        syllabus_text = html_to_text(course.get("syllabus_body"))
        name, name_source = resolve_course_name(course.get("name"), course.get("course_code"), syllabus_text, cid)
        cdir = self.store.open_course(cid, name)
        log.info("scraping %s (%s) -> %s", name, cid, cdir.name)

        ctx = CourseContext(self.client, cid, name, cdir, self.settings.data_dir, self.base, syllabus_text,
                            self.settings.max_file_bytes)
        tabs.load_modules(ctx)

        available = optional(lambda: self.client.list_tabs(cid), "tabs", []) or [
            {"id": t, "label": t.title()} for t in HANDLER_ORDER  # tab list unavailable: try everything
        ]
        by_id = {t["id"]: t for t in available}
        statuses: list[dict] = []
        done_tabs: set[str] = set()

        for tab_id in HANDLER_ORDER:
            if tab_id not in by_id:
                continue
            entry = {"id": tab_id, "label": by_id[tab_id].get("label") or tab_id}
            if tab_id == "files" and not self.include_files:
                entry.update(status="skipped", items=0, note="skipped (--no-files)")
            else:
                try:
                    HANDLERS[tab_id](ctx)
                    n = ctx.items[tab_id]
                    entry.update(status="scraped" if n else "empty", items=n)
                    done_tabs.add(tab_id)
                except CanvasNotAvailable as exc:
                    log.info("tab %s unavailable (%s)", tab_id, exc)
                    entry.update(status="unavailable", items=0, note="not accessible with this token")
            statuses.append(entry)
        if self.include_files and "files" not in done_tabs and ctx.linked_files:
            # Many courses hide the Files tab but still link to files from Modules, pages and
            # assignments. Each linked file can be fetched by its id.
            files.linked_files(ctx)
            done_tabs.add("files")
            entry = next((s for s in statuses if s["id"] == "files"), None)
            if entry is None:
                entry = {"id": "files", "label": "Files"}
                statuses.append(entry)
            entry.update(status="scraped" if ctx.items["files"] else "empty", items=ctx.items["files"],
                         note="Files tab hidden; saved the files the course links to")
        for tab in available:
            if tab["id"] not in HANDLERS:
                note = SKIP_NOTES.get(tab["id"], "external tool, not readable through the Canvas API"
                                      if str(tab["id"]).startswith("context_external_tool") else "not course material")
                statuses.append({"id": tab["id"], "label": tab.get("label") or tab["id"],
                                 "status": "skipped", "items": 0, "note": note})

        write_json(cdir / "facts.json", tabs.facts(ctx, course, cdir))

        module_names = [m.get("name", "") for m, _ in ctx.modules]
        description = html_to_text(course.get("public_description"))
        summary, summary_source, inputs_hash = self._summary(cdir, description, syllabus_text, module_names)
        write_json(cdir / "course.json", {
            "id": cid, "name": name, "name_source": name_source,
            "canvas_name": course.get("name"), "course_code": course.get("course_code"),
            "term": (course.get("term") or {}).get("name"), "default_view": course.get("default_view"),
            "canvas_url": f"{self.base}/courses/{cid}", "folder": cdir.name,
            "summary": summary, "summary_source": summary_source, "summary_inputs_hash": inputs_hash,
            "tabs": statuses, "counts": dict(ctx.by_type),
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        removed = 0
        if self.prune:  # only reached when every handler finished, so a failed run never deletes
            for tab_id in done_tabs:
                removed += prune_tab(cdir, tab_id, ctx.keep[tab_id])
        return CourseResult(cid, name, cdir.name, statuses, dict(ctx.by_type),
                            ctx.written, ctx.unchanged, removed, ctx.downloads)

    def _summary(self, cdir: Path, description: str, syllabus_text: str, module_names: list[str]):
        h = summary_inputs_hash(description, syllabus_text, module_names)
        prev = read_json(cdir / "course.json") or {}
        if prev.get("summary_source") == "llm" and prev.get("summary_inputs_hash") == h:
            return prev["summary"], "llm", h  # keep a nicer summary until the course text changes
        summary, source = extractive_summary(description, syllabus_text, module_names)
        return summary, source, h
