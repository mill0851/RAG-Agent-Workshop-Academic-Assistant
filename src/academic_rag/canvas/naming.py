"""A real name and a short summary for each course, chosen from what Canvas has.

Some Canvas courses are named only by a number ("93756976"). resolve_course_name()
falls back to the syllabus' own title or the course code. extractive_summary() picks
sentences from the course description or the syllabus: no API key needed.
"""

from __future__ import annotations

import hashlib
import re

_GENERIC = {"course", "class", "untitled", "untitled course", "new course", "sandbox", "template"}  # not names
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")  # the space after a . ! or ?


# ----------------------------------------------------------------- the name
def looks_like_id(s: str | None) -> bool:
    """True for names that are really just numbers/codes, e.g. '93756976'."""
    s = (s or "").strip()
    if len(s) < 3 or s.lower() in _GENERIC:
        return True
    if re.fullmatch(r"[\W_]*\d[\d\W_]*", s):  # digits and punctuation only
        return True
    return bool(re.fullmatch(r"(course|class)?[\s_-]*\d{4,}", s, re.I))  # "Course 12345"


def syllabus_title(syllabus_text: str) -> str | None:
    """A title from the syllabus' own heading, e.g. '# Organic Chemistry I Syllabus' gives
    'Organic Chemistry I'. Only the first six lines are looked at."""
    seen = 0
    for line in syllabus_text.splitlines():
        line = line.strip()
        if not line:
            continue
        seen += 1
        if seen > 6:
            break
        if not (line.startswith("#") or re.search(r"syllabus", line, re.I)):
            continue
        title = re.sub(r"^#+\s*", "", line)  # the heading marks
        title = re.sub(r"\bcourse\s+syllabus\b|\bsyllabus\b", "", title, flags=re.I)  # the word itself
        title = re.sub(r"^[\s:\-–—|]+|[\s:\-–—|]+$", "", title)  # punctuation left at either end
        if 4 <= len(title) <= 90 and not looks_like_id(title):
            return title
    return None


def resolve_course_name(
    canvas_name: str | None, course_code: str | None, syllabus_text: str, course_id: str
) -> tuple[str, str]:
    """Returns (name, source). Preference: Canvas name, the syllabus' own title,
    the course code, then 'Course <id>'. Canvas names that are only numbers
    (the raw id) are not considered real names."""
    if not looks_like_id(canvas_name):
        return canvas_name.strip(), "canvas_name"
    title = syllabus_title(syllabus_text)
    if title:
        return title, "syllabus_title"
    if not looks_like_id(course_code):
        return course_code.strip(), "course_code"
    return f"Course {course_id}", "id"


# -------------------------------------------------------------- the summary
def _prose_paragraphs(text: str) -> list[str]:
    """The paragraphs of real sentences: not headings, lists, tables, contact details or links."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if len(para) < 60 or para.startswith(("#", "-", "|")):
            continue
        if "@" in para or para.count("http") > 1:  # contact blocks and link dumps
            continue
        out.append(para)
    return out


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut after a whole sentence when possible."""
    if len(text) <= limit:
        return text
    kept = ""
    for sentence in _SENTENCE_END.split(text):
        if len(kept) + len(sentence) + 1 > limit:
            break
        kept = f"{kept} {sentence}".strip()
    return kept or text[:limit].rsplit(" ", 1)[0] + "..."


def summary_inputs_hash(description: str, syllabus_text: str, module_names: list[str]) -> str:
    """A fingerprint of the text a summary is made from: when that text changes, so does this."""
    h = hashlib.sha256()
    for part in (description, syllabus_text, "\n".join(module_names)):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def extractive_summary(
    description: str, syllabus_text: str, module_names: list[str], max_chars: int = 420
) -> tuple[str, str]:
    """Returns (summary, source). Uses the course's public description when the
    instructor wrote one, else the syllabus' opening prose, plus a topic list from
    the module titles. Pure text selection, so it needs no API key."""
    description = " ".join((description or "").split())
    has_description = len(description) >= 40
    paras = [description] if has_description else _prose_paragraphs(syllabus_text)

    prose = ""
    for para in paras:  # whole paragraphs, until about 60% of the space is used
        prose = f"{prose} {para}".strip()
        if len(prose) >= max_chars * 0.6:
            break
    prose = _cut(prose, max_chars) if prose else ""

    # "Week 3: Integration by Parts" becomes the topic "Integration by Parts"
    topics = [re.sub(r"^(week|unit|module)\s*\d+\s*[:.\-–]\s*", "", m, flags=re.I).strip() for m in module_names]
    topics = [t[:40] for t in topics if t][:6]
    topic_text = f"Topics: {'; '.join(topics)}." if topics else ""

    sources = []
    if prose:
        sources.append("description" if has_description else "syllabus")
    if topic_text:
        sources.append("modules")
    return f"{prose} {topic_text}".strip(), "+".join(sources) or "none"
