"""scrape(): copy your Canvas courses onto this computer, one course at a time. Start here.

scrape() picks the courses, then scrape_course() runs seven steps for each one:

    1. name and folder  a readable name (naming.py), and data/courses/<name> (<id>)/
    2. modules          read first: they say which module each page, quiz and file is in
    3. tabs             ask Canvas which tabs the course shows, then run one handler per
                        tab (tabs.py, files.py); each saves its items as .md files
    4. linked files     if the Files tab is hidden or switched off, save the files the
                        course links to
    5. facts.json       the exact values: due dates, points, your scores and grade
    6. course.json      the course's name, a short summary, and what happened to each tab
    7. prune            delete the items that were deleted in Canvas

If Canvas fails partway through a course, the error stops it before step 7, so a
half-read course never loses files. A tab that is switched off (403 or 404) is skipped,
and only the tabs read completely are pruned.

    client.py    every request to Canvas
    context.py   what one course's steps share, and how each item is saved
    tabs.py      one handler per tab, and facts.json
    files.py     course files: download, extract the text, keep the original
    naming.py    a readable name and a short summary for each course
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from ..config import Settings
from ..course_files import CourseStore, html_to_text, prune_tab, read_json, write_json
from . import files, tabs
from .client import CanvasClient, CanvasNotAvailable
from .context import CourseContext, CourseResult, ScrapeStats, optional
from .naming import extractive_summary, resolve_course_name, summary_inputs_hash

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]  # progress(message, done, total), for the Courses page

# The tabs we read, each with its handler, in the order they run. Modules are loaded
# before all of them (step 2), and Files comes last: by then the other handlers have
# collected every file link in the course's text, which step 4 needs.
HANDLERS = {
    "home": tabs.home,
    "syllabus": tabs.syllabus,
    "modules": tabs.modules,
    "pages": tabs.pages,
    "assignments": tabs.assignments,
    "quizzes": tabs.quizzes,
    "discussions": tabs.discussions,
    "announcements": tabs.announcements,
    "files": files.files,
}

# Tabs Canvas may list that we do not read, and why (shown in course.json)
SKIP_NOTES = {
    "grades": "grades are saved in facts.json",
    "people": "not course material",
    "outcomes": "not course material",
    "rubrics": "rubrics are included with their assignments",
    "chat": "not course material",
    "collaborations": "not readable through the Canvas API",
    "conferences": "not readable through the Canvas API",
}


# --------------------------------------------------------------- start here
def make_canvas(settings: Settings) -> CanvasClient:
    """A client for the Canvas in .env (CANVAS_BASE_URL and CANVAS_API_TOKEN)."""
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
    """Save every course (or just `course_ids`) into data/courses/.

    include_files  False: skip the Files tab (no downloads, and its files are not pruned)
    prune          False: keep items that were deleted in Canvas
    client         a CanvasClient to use instead of the one from .env (tests pass a fake)
    """
    scraper = CanvasScraper(client or make_canvas(settings), settings, CourseStore(settings.courses_dir),
                            course_ids=course_ids, include_files=include_files, prune=prune, progress=progress)
    return scraper.run()


class CanvasScraper:
    """One scrape: which courses, the options, and the steps for each course."""

    def __init__(self, client: CanvasClient, settings: Settings, store: CourseStore, *,
                 course_ids: list[str] | None = None, include_files: bool = True, prune: bool = True,
                 progress: ProgressFn | None = None):
        self.client = client
        self.settings = settings
        self.store = store  # the course folders, data/courses/
        self.course_ids = [str(c) for c in course_ids or settings.course_ids]  # empty: every course
        self.include_files = include_files
        self.prune = prune
        self.progress = progress or (lambda msg, done, total: None)
        self.base = settings.canvas_base_url

    def run(self) -> ScrapeStats:
        """Scrape every selected course, one after another."""
        stats = ScrapeStats()
        courses = self.select_courses()  # a rejected token (CanvasAuthError) stops everything here
        log.info("found %d course(s)", len(courses))
        for i, course in enumerate(courses):
            self.progress(f"Scraping {course.get('name')}", i, len(courses))
            stats.courses.append(self.scrape_course(course))
        self.progress("Scrape finished", len(courses), len(courses))
        return stats

    def select_courses(self) -> list[dict]:
        """The courses to scrape: the ids asked for (CANVAS_COURSE_IDS or --course), or else
        every course you are enrolled in, in CANVAS_ENROLLMENT_STATES (default: active)."""
        wanted = list(dict.fromkeys(self.course_ids))  # without repeats, in order
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

    # ----------------------------------------------------------- one course
    def scrape_course(self, course: dict) -> CourseResult:
        """Save one course. `course` is what Canvas sent for it (client._COURSE_INCLUDES):
        its name, code, term, syllabus, description and your grade."""
        cid = str(course["id"])

        # 1. A readable name, and the course's folder (renamed if the name changed in Canvas)
        syllabus_text = html_to_text(course.get("syllabus_body"))
        name, name_source = resolve_course_name(course.get("name"), course.get("course_code"), syllabus_text, cid)
        cdir = self.store.open_course(cid, name)
        log.info("scraping %s (%s) -> %s", name, cid, cdir.name)
        ctx = CourseContext(self.client, cid, name, cdir, self.settings.data_dir, self.base, syllabus_text,
                            self.settings.max_file_bytes)

        # 2. Modules first: every item looks up its module in them
        tabs.load_modules(ctx)

        # 3. Every tab the course shows that we can read. If Canvas will not list the
        #    tabs, try every handler: a tab that is switched off just answers 403 or 404.
        shown = optional(lambda: self.client.list_tabs(cid), "tabs", []) or [
            {"id": t, "label": t.title()} for t in HANDLERS
        ]
        statuses, done = self._read_tabs(ctx, shown)

        # 4. The Files tab is hidden or switched off: save the files the course links to
        if self.include_files and "files" not in done and ctx.linked_files:
            self._save_linked_files(ctx, statuses)
            done.add("files")
        statuses += _skipped_tabs(shown)

        # 5. facts.json
        write_json(cdir / "facts.json", tabs.facts(ctx, course, cdir))

        # 6. course.json
        write_json(cdir / "course.json", self._course_json(ctx, course, name_source, statuses))

        # 7. Prune: in each tab that was read completely, delete the files not saved this
        #    run (the items deleted in Canvas). Your uploads/ folder is never touched.
        removed = 0
        if self.prune:
            for tab_id in done:
                removed += prune_tab(cdir, tab_id, ctx.keep[tab_id])
        return CourseResult(cid, name, cdir.name, statuses, dict(ctx.by_type),
                            ctx.written, ctx.unchanged, removed, ctx.downloads)

    def _read_tabs(self, ctx: CourseContext, shown: list[dict]) -> tuple[list[dict], set[str]]:
        """Step 3: run the handler of every tab the course shows. Returns each tab's status
        for course.json, and the tabs that were read completely (only those are pruned)."""
        by_id = {t["id"]: t for t in shown}
        statuses: list[dict] = []
        done: set[str] = set()
        for tab_id, handler in HANDLERS.items():
            if tab_id not in by_id:
                continue  # the course does not show this tab
            status = {"id": tab_id, "label": by_id[tab_id].get("label") or tab_id}
            if tab_id == "files" and not self.include_files:
                status.update(status="skipped", items=0, note="skipped (--no-files)")
            else:
                try:
                    handler(ctx)
                    n = ctx.items[tab_id]
                    status.update(status="scraped" if n else "empty", items=n)
                    done.add(tab_id)
                except CanvasNotAvailable as exc:  # listed, but switched off for students
                    log.info("tab %s unavailable (%s)", tab_id, exc)
                    status.update(status="unavailable", items=0, note="not accessible with this token")
            statuses.append(status)
        return statuses, done

    def _save_linked_files(self, ctx: CourseContext, statuses: list[dict]) -> None:
        """Step 4: fetch each linked file by its id, and report it as the Files tab."""
        files.linked_files(ctx)
        status = next((s for s in statuses if s["id"] == "files"), None)
        if status is None:  # Canvas did not list a Files tab at all
            status = {"id": "files", "label": "Files"}
            statuses.append(status)
        n = ctx.items["files"]
        status.update(status="scraped" if n else "empty", items=n,
                      note="Files tab hidden; saved the files the course links to")

    def _course_json(self, ctx: CourseContext, course: dict, name_source: str, statuses: list[dict]) -> dict:
        """Step 6: what the Courses page and the agent know about the course."""
        module_names = [m.get("name", "") for m, _ in ctx.modules]
        description = html_to_text(course.get("public_description"))
        summary, summary_source, inputs_hash = self._summary(ctx, description, module_names)
        return {
            "id": ctx.cid, "name": ctx.name, "name_source": name_source,
            "canvas_name": course.get("name"), "course_code": course.get("course_code"),
            "term": (course.get("term") or {}).get("name"), "default_view": course.get("default_view"),
            "canvas_url": f"{self.base}/courses/{ctx.cid}", "folder": ctx.cdir.name,
            "summary": summary, "summary_source": summary_source, "summary_inputs_hash": inputs_hash,
            "tabs": statuses, "counts": dict(ctx.by_type),
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _summary(self, ctx: CourseContext, description: str, module_names: list[str]) -> tuple[str, str, str]:
        """A short summary picked from the course's own text (naming.py). A summary written
        by an AI ("llm") is kept until the text it was written from changes."""
        h = summary_inputs_hash(description, ctx.syllabus_text, module_names)
        prev = read_json(ctx.cdir / "course.json") or {}
        if prev.get("summary_source") == "llm" and prev.get("summary_inputs_hash") == h:
            return prev["summary"], "llm", h
        summary, source = extractive_summary(description, ctx.syllabus_text, module_names)
        return summary, source, h


def _skipped_tabs(shown: list[dict]) -> list[dict]:
    """The tabs the course shows that we do not read, each with the reason."""
    statuses = []
    for tab in shown:
        if tab["id"] in HANDLERS:
            continue
        if tab["id"] in SKIP_NOTES:
            note = SKIP_NOTES[tab["id"]]
        elif str(tab["id"]).startswith("context_external_tool"):  # Piazza, Zoom, Gradescope, ...
            note = "external tool, not readable through the Canvas API"
        else:
            note = "not course material"
        statuses.append({"id": tab["id"], "label": tab.get("label") or tab["id"],
                         "status": "skipped", "items": 0, "note": note})
    return statuses
