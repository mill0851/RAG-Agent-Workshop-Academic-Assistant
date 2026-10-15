"""What one course's steps share while it is scraped (CourseContext), how each item is
saved (CourseContext.emit), and the numbers a scrape reports (CourseResult, ScrapeStats)."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..course_files import Segment, write_item
from .client import CanvasClient, CanvasNotAvailable

log = logging.getLogger(__name__)


# ---------------------------------------------------- what a scrape reports
@dataclass
class CourseResult:
    """One course's outcome: what `acad scrape` prints and POST /scrape returns."""
    id: str
    name: str
    folder: str
    tabs: list[dict]  # one status per tab, as saved in course.json
    counts: dict  # items saved per type ("page": 3, "file": 12, ...)
    written: int = 0  # items new or changed this run
    unchanged: int = 0  # items identical to the saved copy
    removed: int = 0  # items deleted because they are gone from Canvas
    downloads: int = 0  # files downloaded (unchanged files are not downloaded again)


@dataclass
class ScrapeStats:
    """Every course's outcome, and the totals across them."""

    courses: list[CourseResult] = field(default_factory=list)

    @property
    def written(self) -> int:
        """Items new or changed, in every course."""
        return sum(c.written for c in self.courses)

    @property
    def unchanged(self) -> int:
        """Items identical to the saved copy, in every course."""
        return sum(c.unchanged for c in self.courses)

    @property
    def removed(self) -> int:
        """Items deleted because they are gone from Canvas, in every course."""
        return sum(c.removed for c in self.courses)

    @property
    def downloads(self) -> int:
        """Files downloaded, in every course."""
        return sum(c.downloads for c in self.courses)

    def summary(self) -> str:
        """One line: what `acad scrape` prints at the end."""
        return (f"{len(self.courses)} course(s): {self.written} items saved or updated, "
                f"{self.unchanged} unchanged, {self.removed} removed, {self.downloads} files downloaded")


def optional(fn, what: str, default):
    """Run one Canvas call. If Canvas answers 403 or 404 (a disabled tab, a hidden item),
    return `default` instead: that just means "nothing here"."""
    try:
        return fn()
    except CanvasNotAvailable as exc:
        log.info("skipping %s (%s)", what, exc)
        return default


# --------------------------------------------------- one course in progress
class CourseContext:
    """Everything one course's steps share: the client, where to save, and what was saved."""

    def __init__(self, client: CanvasClient, cid: str, name: str, cdir: Path, data_root: Path, base: str,
                 syllabus_text: str, max_file_bytes: int):
        # The course, and where it is saved
        self.client = client
        self.cid = cid  # the Canvas course id, e.g. "41001"
        self.name = name  # its readable name (naming.py)
        self.cdir = cdir  # data/courses/<name> (<id>)/
        self.data_root = data_root  # data/
        self.base = base  # https://canvas.<school>.edu, for links back to Canvas
        self.syllabus_text = syllabus_text  # the Syllabus tab, already turned into text
        self.max_file_bytes = max_file_bytes  # larger course files are skipped

        # Filled in by tabs.load_modules(), before any tab is read
        self.modules: list[tuple[dict, list[dict]]] = []  # (module, its items), in course order
        self.module_of: dict[str, str] = {}  # "page:<url>" or "<kind>:<id>" -> the module's name
        self.linked_files: set[str] = set()  # ids of files the course links to, from modules and texts

        # Filled in by the tab handlers
        self.keep: dict[str, set[str]] = defaultdict(set)  # tab -> paths saved this run (the rest is pruned)
        self.items: Counter = Counter()  # tab -> items saved
        self.by_type: Counter = Counter()  # item type -> items saved
        self.written = self.unchanged = self.downloads = 0
        self.assignments: list[dict] | None = None  # facts.json entries; None means the tab was not read
        self.quizzes: list[dict] | None = None

        # A link to a course file inside a page or an assignment: .../courses/41001/files/123
        self._file_link = re.compile(rf"/courses/{cid}/files/(\d+)")

    def module_for(self, kind: str, key) -> str:
        """The name of the module an item is in, or "" (tabs.load_modules() fills this in)."""
        return self.module_of.get(f"{kind}:{key}", "")

    def emit(self, tab: str, path: str, *, kind: str, canvas_id, title: str, url: str, content,
             updated: str = "", module: str = "", extra: dict | None = None, allow_empty: bool = False) -> None:
        """Save one item as <course folder>/<path>, unless it has no text (allow_empty saves it
        anyway). The file is rewritten only when its content changed.

            tab        the tab it came from: what is pruned when it is gone from Canvas
            kind       its type: page, assignment, file, ... (the index can filter on it)
            canvas_id  its id in Canvas, which makes its doc_id
            content    its text, or a list of Segments (one per page or slide of a file)
        """
        segments = [Segment(content)] if isinstance(content, str) else list(content)
        if not allow_empty and not any(s.text.strip() for s in segments):
            return
        for s in segments:  # remember the course files this text links to (files.linked_files)
            self.linked_files.update(self._file_link.findall(s.text))
        meta = {  # the JSON header at the top of the saved .md file
            "doc_id": f"course:{self.cid}:{kind}:{canvas_id}", "course_id": self.cid, "course_name": self.name,
            "source_type": kind, "title": title or kind.title(), "url": url, "module": module,
            "updated_at": updated or "", "canvas_id": str(canvas_id), **(extra or {}),
        }
        if write_item(self.cdir / path, meta, segments):
            self.written += 1
        else:
            self.unchanged += 1
        self.keep[tab].add(path)
        self.items[tab] += 1
        self.by_type[kind] += 1
