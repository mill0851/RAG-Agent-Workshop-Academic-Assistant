"""The three tools every question gets: search the course by meaning, find exact text,
and read a whole document. Each returns plain text, written for the model to read."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import course_files
from ..index import format_passages, search
from .tool import Tool

if TYPE_CHECKING:
    from .toolbox import Toolbox

SEARCH_RESULTS = 10  # passages per search_course call
FIND_HITS = 30  # matching lines per find_text call
READ_CHARS = 30_000  # a longer document is cut here


def search_course(box: Toolbox, query: str) -> str:
    """Vector search again, with the model's own words (the same search as the first pass)."""
    passages = search(box.store, box.embedder, query, SEARCH_RESULTS, course_id=box.course_id)
    if not passages:
        return f'Nothing in the course material matches "{query}".'
    return format_passages(passages, numbered=False)


def _squash(text: str) -> str:
    return "".join(text.split()).lower()  # so "HW 3" finds "HW3" and "HW  3" too


def find_text(box: Toolbox, text: str) -> str:
    """Exact text, like Ctrl+F across every saved item: catches codes that meaning-based
    search blurs. Spacing and capitals are ignored."""
    needle = _squash(text)
    if len(needle) < 2:
        return "Give at least two characters to look for."
    hits = []
    for folder in box.folders():
        for doc in course_files.iter_documents(folder, box.settings.data_dir):
            for seg in doc.segments:
                for line in seg.text.splitlines():
                    if needle in _squash(line):
                        where = f"{doc.title} ({seg.label})" if seg.label else doc.title
                        hits.append(f"- {where} | file: {doc.rel_path}\n  {line.strip()[:300]}")
                        if len(hits) == FIND_HITS:
                            return "\n".join(hits) + f"\n(the first {FIND_HITS} matches; look for something more specific)"
    return "\n".join(hits) if hits else f'No course text contains "{text}".'


def read_document(box: Toolbox, path: str) -> str:
    """One whole saved item, with its page labels, from inside the course's folder only."""
    target = (box.settings.data_dir / path.strip().lstrip("/\\")).resolve()
    if target.suffix != ".md":  # the original's name ("HW2.pdf") works too
        target = target.with_name(target.name + ".md")
    if not any(target.is_relative_to(folder.resolve()) for folder in box.folders()):
        return "That is not a document of this course. Use a file path from the sources, the file list or find_text."
    doc = course_files.read_document(target, box.settings.data_dir)
    if doc is None:
        return f"There is no document at {path}."
    text ="\n\n".join(f"({s.label}) {s.text}" if s.label else s.text for s in doc.segments)
    if len(text) > READ_CHARS:
        text = text[:READ_CHARS] + f"\n\n[cut at {READ_CHARS} characters; use find_text to reach later parts]"
    return (f"{doc.title} | {doc.course_name} | {doc.source_type}\nfile: {doc.rel_path}\n\n"
            f"{text or '(no text: a scanned PDF or an image)'}")


SEARCH_COURSE = Tool(
    name="search_course",
    description=(
        "Search the course material by meaning and return the most relevant passages, each with its "
        "file path. Use it when the numbered sources do not answer the question: try other words, a "
        "narrower topic, or the name of a specific assignment, lecture or chapter."
    ),
    input_schema={"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "What to look for, in a few words"}}},
    run=search_course,
)

FIND_TEXT = Tool(
    name="find_text",
    description=(
        "Find every line of the course material that contains this exact text (capitals and spaces are "
        "ignored), with its document, page and file path. Use it for exact names, numbers and codes that a search "
        "by meaning can miss, such as 'Problem 3.5', 'HW 3', 'Section 2.4' or a theorem's name."
    ),
    input_schema={"type": "object", "required": ["text"], "properties": {
        "text": {"type": "string", "description": "The exact text to look for"}}},
    run=find_text,
)

READ_DOCUMENT = Tool(
    name="read_document",
    description=(
        "Read a whole course document (an assignment, a homework PDF, a lecture's slides, the syllabus) "
        "with its page labels. Use it when a passage is only a fragment of what you need. Pass the file "
        "path shown in the sources, the course's file list, or find_text."
    ),
    input_schema={"type": "object", "required": ["path"], "properties": {
        "path": {"type": "string", "description": "The file path, e.g. courses/Physics I (123)/files/HW2.pdf.md"}}},
    run=read_document,
)
