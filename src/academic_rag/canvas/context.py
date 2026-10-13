"""What one course's tab handlers share while it is scraped (CourseContext), and the
numbers a scrape reports (CourseResult, ScrapeStats)."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..course_files import Segment, write_item
from .client import CanvasClient, CanvasNotAvailable

log = logging.getLogger(__name__)


@dataclass
class CourseResult:
    id: str
    name: str
    folder: str
    tabs: list[dict]
    counts: dict
    written: int = 0
    unchanged: int = 0
    removed: int = 0
    downloads: int = 0


@dataclass
class ScrapeStats:
    courses: list[CourseResult] = field(default_factory=list)

    @property
    def written(self) -> int:
        return sum(c.written for c in self.courses)

    @property
    def unchanged(self) -> int:
        return sum(c.unchanged for c in self.courses)

    @property
    def removed(self) -> int:
        return sum(c.removed for c in self.courses)

    @property
    def downloads(self) -> int:
        return sum(c.downloads for c in self.courses)

    def summary(self) -> str:
        return (f"{len(self.courses)} course(s): {self.written} items saved or updated, "
                f"{self.unchanged} unchanged, {self.removed} removed, {self.downloads} files downloaded")


def optional(fn, what: str, default):
    """Run a Canvas call; a disabled tab or hidden item (403/404) just means 'nothing here'."""
    try:
        return fn()
    except CanvasNotAvailable as exc:
        log.info("skipping %s (%s)", what, exc)
        return default


class CourseContext:
    """Everything one course's handlers share: the client, where to save, and what was saved."""

    def __init__(self, client: CanvasClient, cid: str, name: str, cdir: Path, data_root: Path, base: str,
                 syllabus_text: str, max_file_bytes: int):
        self.client, self.cid, self.name, self.cdir, self.data_root = client, cid, name, cdir, data_root
        self.base, self.syllabus_text, self.max_file_bytes = base, syllabus_text, max_file_bytes
        self.keep: dict[str, set[str]] = defaultdict(set)  # tab -> paths relative to cdir
        self.items: Counter = Counter()  # tab -> item count
        self.by_type: Counter = Counter()
        self.written = self.unchanged = self.downloads = 0
        self.modules: list[tuple[dict, list[dict]]] = []
        self.module_of: dict[str, str] = {}
        self.assignments: list[dict] | None = None
        self.quizzes: list[dict] | None = None
        self.linked_files: set[str] = set()  # ids of files the course links to (Modules, pages, ...)
        self._file_link = re.compile(rf"/courses/{cid}/files/(\d+)")

    def emit(self, tab: str, rel: str, kind: str, key, title: str, url: str, content,
             updated: str = "", module: str = "", extra: dict | None = None, allow_empty: bool = False) -> None:
        """Save one item as <course folder>/<rel> and remember it was seen this run."""
        segments = [Segment(content)] if isinstance(content, str) else list(content)
        if not allow_empty and not any(s.text.strip() for s in segments):
            return
        for s in segments:  # links like .../courses/41001/files/123 point at course files
            self.linked_files.update(self._file_link.findall(s.text))
        meta = {
            "doc_id": f"course:{self.cid}:{kind}:{key}", "course_id": self.cid, "course_name": self.name,
            "source_type": kind, "title": title or kind.title(), "url": url, "module": module,
            "updated_at": updated or "", "canvas_id": str(key), **(extra or {}),
        }
        if write_item(self.cdir / rel, meta, segments):
            self.written += 1
        else:
            self.unchanged += 1
        self.keep[tab].add(rel)
        self.items[tab] += 1
        self.by_type[kind] += 1
