"""Step 1 of a turn: what the model gets before it starts, the "seed".

    gather()             everything below, for one question
    small_to_big()       swap a fragment for its whole document (short ones) or its whole page
    course_file_list()   every item in the course, with the path read_document opens

The seed is classic RAG: embed the question and take the nearest chunks. It is sized to
answer most questions in one step. When it is not enough, the model calls the tools
(tools/toolbox.py) to search again, find exact text, or read whole documents.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..config import Settings
from ..course_files import Document, list_courses, list_documents, read_document
from ..index import Embedder, Passage, VectorStore, search
from ..tools import due_soon

PASSAGES = 20  # search results per question (the Search page shows 6)
WHOLE_DOCUMENT_CHARS = 6000  # a document shorter than this is sent whole instead of in fragments
WHOLE_PAGE_CHARS = 6000  # in a longer document, a page or slide shorter than this is sent whole
CONTEXT_CHARS = 60000  # stop adding sources beyond this much text
FILE_LIST_TITLES = 200
DEADLINE_DAYS = 14  # every question gets what is due in this many days


@dataclass
class Context:
    passages: list[Passage]  # numbered sources, best first
    deadlines: list[dict]  # what is due in the next DEADLINE_DAYS days (tools/due_soon.py)
    course_files: list[str]  # "file: HW2 2026.pdf | courses/.../HW2 2026.pdf.md" (or course names)
    course_chosen: bool = True  # False: "All courses" on the Ask page


def gather(question: str, *, settings: Settings, store: VectorStore, embedder: Embedder,
           history: list[dict] = (), course_id: str | None = None) -> Context:
    # A follow-up like "and the second one?" means little alone, so search it together with
    # the conversation's first question, which names the topic.
    first = next((t["text"] for t in history if t["role"] == "student"), "")
    hits = search(store, embedder, f"{first} {question}".strip(), PASSAGES, course_id=course_id)
    return Context(
        passages=small_to_big(hits, settings),
        deadlines=due_soon(settings, DEADLINE_DAYS, course_id=course_id),
        course_files=course_file_list(settings, course_id),
        course_chosen=bool(course_id),
    )


def small_to_big(hits: list[Passage], settings: Settings) -> list[Passage]:
    """Search matches small chunks, because they are precise; the model reads the bigger
    unit each one came from. Each hit becomes, at the rank of its best fragment:
    - its whole document, if the document is short (a homework sheet, an announcement);
    - else its whole page or slide (a page of a 50-page lecture or a textbook);
    - else the fragment itself (a long page with no page numbers, or a huge page).
    The text comes from the saved item, which has each page once (chunks overlap).
    Numbered again from 1."""
    sources: list[Passage] = []
    done: set[tuple[str, str]] = set()  # (file, page); page "" means the whole document
    docs: dict[str, Document | None] = {}
    total = 0
    for hit in hits:
        if hit.path not in docs:
            docs[hit.path] = read_document(settings.data_dir / hit.path, settings.data_dir)
        doc = docs[hit.path]
        if (hit.path, "") in done or (hit.path, hit.location) in done:
            continue  # its document or page is already in
        source = hit
        if doc is not None and len(doc.text) <= WHOLE_DOCUMENT_CHARS:
            text = "\n\n".join(f"({s.label}) {s.text}" if s.label else s.text for s in doc.segments)
            source = replace(hit, text=text, location="")
            done.add((hit.path, ""))
        elif doc is not None and hit.location:
            page = next((s for s in doc.segments if s.label == hit.location), None)
            if page is not None and len(page.text) <= WHOLE_PAGE_CHARS:
                source = replace(hit, text=page.text)
                done.add((hit.path, hit.location))
        if total + len(source.text) > CONTEXT_CHARS and sources:
            break
        total += len(source.text)
        sources.append(source)
    return [replace(s, n=n) for n, s in enumerate(sources, start=1)]


def course_file_list(settings: Settings, course_id: str | None) -> list[str]:
    if not course_id:
        return [f"course: {c['name']}" for c in list_courses(settings)]
    docs = list_documents(settings, course_id) or []
    return [f"{d['type']}: {d['title']} | {d['path']}" for d in docs][:FILE_LIST_TITLES]
