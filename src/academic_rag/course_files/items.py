"""One saved item per .md file: a JSON header between '---' lines, then the text,
with a marker before each page or slide. It reads fine in any editor and carries what
the embedder and the front end need.

    ---
    {"doc_id": "course:123:file:456", "title": "Lecture 9.pdf", "source_type": "file", ...}
    ---

    # Lecture 9.pdf

    <!-- segment: p. 1 -->

    Work done by a constant force ...

    write_item()       save an item, only when it changed
    read_meta()        just the header of an item
    read_document()    a whole item back: its header and its labelled Segments
    iter_documents()   every item in a course folder
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .extract import Segment
from .folders import write_bytes

_SEGMENT = re.compile(r"^<!-- segment: (.*?) -->[ \t]*$", re.M)  # the marker before each page or slide


@dataclass
class Document:
    """One saved item (a page, an assignment, a PDF, an upload, ...)."""

    doc_id: str  # stable across runs, e.g. "course:123:file:456"
    course_id: str
    course_name: str
    source_type: str  # home | syllabus | module | page | assignment | announcement | discussion | quiz | file | upload
    title: str
    url: str  # human-facing Canvas link
    segments: list[Segment] = field(default_factory=list)
    module: str = ""
    updated_at: str = ""
    rel_path: str = ""  # path of the markdown file inside the data dir (for the front end)

    @property
    def text(self) -> str:
        """All of its text, the Segments joined by blank lines."""
        return "\n\n".join(s.text for s in self.segments if s.text.strip())

    @property
    def content_hash(self) -> str:
        """A fingerprint of what gets embedded: when it changes, the item is embedded again."""
        h = hashlib.sha256()
        for part in (self.course_name, self.title, self.url, self.module, self.text):
            h.update(part.encode("utf-8", "replace"))
            h.update(b"\x00")
        return h.hexdigest()


# ------------------------------------------------------------------ writing
def render_item(meta: dict, segments: list[Segment]) -> str:
    """The text of an item file: the header, the title as a heading, then each Segment,
    with a marker before each one that has a label."""
    parts = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        parts.append(f"<!-- segment: {seg.label} -->\n\n{text}" if seg.label else text)
    body = "\n\n".join(parts)
    head = json.dumps(meta, indent=2, ensure_ascii=False)
    return f"---\n{head}\n---\n\n# {meta.get('title', '')}\n\n{body}\n"


def write_item(path: Path, meta: dict, segments: list[Segment]) -> bool:
    """Write an item; returns False (and leaves the file untouched) if nothing changed."""
    content = render_item(meta, segments)
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    write_bytes(path, content.encode("utf-8"))
    return True


# ------------------------------------------------------------------ reading
def _split(text: str) -> tuple[dict, str]:
    """An item file's text as (its header, the rest). ValueError if it has no header."""
    if not text.startswith("---\n"):
        raise ValueError("missing header")
    end = text.index("\n---\n", 4)
    return json.loads(text[4:end]), text[end + 5 :]


def read_meta(path: Path) -> dict | None:
    """Just an item's header, or None for a file that is not one of our items."""
    try:
        return _split(path.read_text(encoding="utf-8"))[0]
    except (OSError, ValueError):  # missing, binary (a PDF original), or not one of ours
        return None


def read_document(path: Path, data_root: Path, course_name: str | None = None) -> Document | None:
    """Parse one item file back into a Document. Returns None for files that are
    not ours (no header) so a stray note dropped in a course folder is ignored."""
    try:
        meta, body = _split(path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError, OSError):
        return None
    if "doc_id" not in meta:
        return None

    # The body without its title heading, cut at the segment markers
    title = meta.get("title", "")
    body = body.strip()
    if body.startswith(f"# {title}"):
        body = body[len(f"# {title}") :].strip()
    parts = _SEGMENT.split(body)  # [pre, label1, text1, label2, text2, ...]
    segments = [Segment(parts[0].strip())] if parts[0].strip() else []
    for label, text in zip(parts[1::2], parts[2::2]):
        if text.strip():
            segments.append(Segment(text.strip(), label.strip()))

    try:
        rel = path.relative_to(data_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return Document(
        doc_id=meta["doc_id"],
        course_id=str(meta.get("course_id", "")),
        course_name=course_name or meta.get("course_name", ""),
        source_type=meta.get("source_type", ""),
        title=title,
        url=meta.get("url", ""),
        segments=segments,
        module=meta.get("module", ""),
        updated_at=meta.get("updated_at", ""),
        rel_path=rel,
    )


def iter_documents(course_dir: Path, data_root: Path, course_name: str | None = None) -> Iterator[Document]:
    """Every item in a course folder, in path order (files that are not items are skipped)."""
    for path in sorted(course_dir.rglob("*.md")):
        doc = read_document(path, data_root, course_name)
        if doc is not None:
            yield doc


def count_markdown(course_dir: Path) -> int:
    """How many .md files a course folder holds (for progress: "12 of 340")."""
    return sum(1 for _ in course_dir.rglob("*.md"))
