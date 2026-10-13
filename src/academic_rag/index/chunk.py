"""Step 1 of building the index: cut each saved item into chunks of about CHUNK_CHARS
characters, on paragraph, then sentence, then word boundaries. Each chunk keeps its
page or slide label, and is embedded with a short header (course > module > title),
so a fragment still says where it came from.

CHUNK_CHARS is the target; the embedding model's limit is the hard cap. The model counts
tokens, not characters, and math has more tokens per character than prose, so a chunk
the model cannot read whole is cut in two until every piece fits (embedder.fits)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..course_files import Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)")
_HEADING_ONLY = re.compile(r"^#{1,6}\s+[^\n]*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_BREAKS = (re.compile(r"\n\s*\n"), _SENTENCE, re.compile(r"\n"), re.compile(r" "))  # best first


@dataclass
class Chunk:
    chunk_id: str
    text: str  # the text shown to the model
    embed_text: str  # header + text, what actually gets embedded
    metadata: dict


def split_text(text: str, size: int, overlap: int) -> list[tuple[str, str]]:
    """Split text into (chunk, section_heading) pairs of at most ~size chars,
    breaking on paragraph, then sentence, then word boundaries."""
    if size <= overlap:
        raise ValueError("chunk size must be larger than overlap")

    units: list[tuple[str, str]] = []  # (text, heading in effect)
    heading = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        m = _HEADING.match(para)
        if m:
            heading = m.group(2).strip()
        for piece in _fit(para, size):
            units.append((piece, heading))

    chunks: list[tuple[str, str]] = []
    cur, cur_heading = "", ""
    for unit, h in units:
        if not cur:
            cur, cur_heading = unit, h
        elif len(cur) + 2 + len(unit) <= size or _HEADING_ONLY.match(cur):
            # a lone heading always stays glued to the text it introduces
            cur += "\n\n" + unit
        else:
            chunks.append((cur, cur_heading))
            tail = _tail(cur, overlap)
            cur = f"{tail}\n\n{unit}" if tail and len(tail) + 2 + len(unit) <= size else unit
            cur_heading = h
    if cur:
        chunks.append((cur, cur_heading))
    return chunks


def _fit(para: str, size: int) -> list[str]:
    """Break one paragraph into pieces no longer than size."""
    if len(para) <= size:
        return [para]
    pieces, cur = [], ""
    for sentence in _SENTENCE.split(para):
        if len(sentence) > size:  # no sentence boundary: hard split on whitespace
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.extend(_hard_split(sentence, size))
        elif cur and len(cur) + 1 + len(sentence) > size:
            pieces.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def _hard_split(text: str, size: int) -> list[str]:
    out = []
    while len(text) > size:
        cut = text.rfind(" ", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def _tail(text: str, n: int) -> str:
    """Last ~n chars of text, starting on a word boundary; '' if text is shorter."""
    if n <= 0 or len(text) <= n:
        return ""
    tail = text[-n:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def _shrink(header: str, text: str, fits: Callable[[str], bool] | None) -> list[str]:
    """Cut text in two, again and again, until the model can read each piece with its header."""
    if fits is None or fits(f"{header}\n{text}"):
        return [text]
    first, second = _halve(text)
    if not first or not second:  # nothing left to cut
        return [text]
    return _shrink(header, first, fits) + _shrink(header, second, fits)


def _halve(text: str) -> tuple[str, str]:
    """Cut at a paragraph, sentence, line or word break in the middle half of the text."""
    mid, quarter = len(text) // 2, len(text) // 4
    for brk in _BREAKS:
        cuts = [m for m in brk.finditer(text) if abs(m.start() - mid) <= quarter]
        if cuts:
            m = min(cuts, key=lambda m: abs(m.start() - mid))
            return text[: m.start()].strip(), text[m.end() :].strip()
    return text[:mid].strip(), text[mid:].strip()


def chunk_document(doc: Document, size: int, overlap: int,
                   fits: Callable[[str], bool] | None = None) -> list[Chunk]:
    """Chunks of about `size` characters; with `fits` (the embedder's), none longer than
    the model can read."""
    chunks: list[Chunk] = []
    for seg in doc.segments:
        for text, section in split_text(seg.text, size, overlap):
            header = " > ".join(b for b in (doc.course_name, doc.module, doc.title) if b)
            if seg.label:
                header += f" ({seg.label})"
            if section and section != doc.title:
                header += f" / {section}"
            for piece in _shrink(header, text, fits):
                i = len(chunks)
                chunks.append(Chunk(
                    chunk_id=f"{doc.doc_id}#{i}",
                    text=piece,
                    embed_text=f"{header}\n{piece}",
                    metadata={
                        "doc_id": doc.doc_id, "course_id": doc.course_id, "course_name": doc.course_name,
                        "source_type": doc.source_type, "title": doc.title, "url": doc.url,
                        "path": doc.rel_path, "module": doc.module, "location": seg.label,
                        "section": section, "updated_at": doc.updated_at, "chunk_index": i,
                    },
                ))
    return chunks
