"""Query the index: a question in, numbered passages ready to cite out.

    search               embed the question, fetch the nearest chunks, rerank, number them
    boost_exact_matches  TASK 3: lift passages that contain the question's exact codes
    resolve_course       "organic" -> "102"
    format_passages      the [n] text block the CLI prints and the agent reads
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .embed import Embedder
from .store import SOURCE_TYPES, Hit, VectorStore

CANDIDATES_PER_RESULT = 3  # fetch 3x what we return, so a reranker has something to promote


@dataclass
class Passage:
    n: int  # citation number, 1-based within one search
    title: str
    course: str
    course_id: str
    type: str
    location: str  # "p. 3" / "slide 5" / ""
    module: str
    url: str  # link back to Canvas ("" for uploads)
    path: str  # saved markdown file, relative to the data dir
    score: float  # cosine similarity, higher is more similar
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def search(
    store: VectorStore,
    embedder: Embedder,
    query: str,
    k: int = 6,
    *,
    course_id: str | None = None,
    source_types: list[str] | None = None,
) -> list[Passage]:
    query = (query or "").strip()
    if not query:
        return []
    store.check()
    k = max(1, min(k, 20))
    types = [t for t in (source_types or []) if t in SOURCE_TYPES] or None

    # 1. Retrieve wide: the nearest chunks by meaning (cosine similarity of embeddings).
    hits = store.query(embedder.embed_query(query), k * CANDIDATES_PER_RESULT,
                       course_id=course_id, source_types=types)
    # 2. Rerank, then keep the best k and number them for citation.
    passages = boost_exact_matches(query, [_passage(h) for h in hits])[:k]
    for n, p in enumerate(passages, start=1):
        p.n = n
    return passages


def boost_exact_matches(query: str, passages: list[Passage]) -> list[Passage]:
    """TASK 3 (see index/README.md).

    Embeddings capture meaning, not exact strings, so "when is homework 11.3 due" can
    rank passages that are *about* homework above the one that says "Homework 11.3".
    Return the same passages, reordered so that ones containing the query's exact
    codes and numbers (HW 2, CHEM 2053, Quiz 1, 11.3) come first. Among passages that
    match equally, keep the vector order. Never drop a passage.

    SOLUTION: find the exact terms, count whole-term matches per passage, and sort by
    that count (sorted() is stable, so ties keep the vector order).
    """
    # SOLUTION
    # 1. the exact terms: a number, plus the word before it ("hw 2", "chem 2053", "homework 11.3")
    terms = {m.group(0) for m in _EXACT_TERM.finditer(query.lower())}
    if not terms:
        return passages
    patterns = [re.compile(rf"\b{re.escape(t)}\b") for t in terms]

    # 2. how many of the terms a passage contains, as whole terms, in its title or text
    def matches(p: Passage) -> int:
        text = " ".join(f"{p.title} {p.text}".lower().split())  # lowercase, spaces collapsed
        return sum(1 for pattern in patterns if pattern.search(text))

    # 3. most matches first; sorted() is stable, so ties keep the vector order
    return sorted(passages, key=matches, reverse=True)


_EXACT_TERM = re.compile(r"(?:[a-z]+ )?\d+(?:\.\d+)*[a-z]?")


def _passage(h: Hit) -> Passage:
    m = h.metadata
    return Passage(
        n=0, title=m.get("title", ""), course=m.get("course_name", ""), course_id=m.get("course_id", ""),
        type=m.get("source_type", ""), location=m.get("location", ""), module=m.get("module", ""),
        url=m.get("url", ""), path=m.get("path", ""), score=round(h.score, 4), text=h.text,
    )


def resolve_course(courses: list[dict], spec: str) -> tuple[str | None, str | None]:
    """Turn a course id or part of a course name into an id, using the entries from
    course_files.list_courses(). Returns (course_id, problem); exactly one is None."""
    spec = spec.strip()
    for c in courses:
        if c["id"] == spec:
            return c["id"], None
    matches = [c for c in courses if spec.lower() in (c["name"] or "").lower()]
    if len(matches) == 1:
        return matches[0]["id"], None
    pool = matches or courses
    label = "Ambiguous course" if matches else f"No course matches '{spec}'"
    listing = "; ".join(f"{c['name']} (id {c['id']})" for c in pool)
    return None, f"{label}. Choose one of: {listing}"


def format_passages(passages: list[Passage], max_chars: int | None = None, numbered: bool = True) -> str:
    """Human-readable block for the CLI and the agent: [n] citation numbers (or "-" for
    tool results, which are cited by file name), and the saved file a tool can open."""
    blocks = []
    for p in passages:
        where = " > ".join(x for x in (p.course, p.module) if x)
        title = f"{p.title} ({p.location})" if p.location else p.title
        body = p.text if max_chars is None or len(p.text) <= max_chars else p.text[:max_chars].rsplit(" ", 1)[0] + " ..."
        link = f"\nlink: {p.url}" if p.url else ""
        mark = f"[{p.n}]" if numbered else "-"
        blocks.append(f"{mark} {title} | {where} | {p.type} | score {p.score:.2f}\nfile: {p.path}{link}\n{body}")
    return "\n\n".join(blocks)
