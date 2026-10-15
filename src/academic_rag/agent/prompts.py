"""Step 2 of a turn: everything the model is told, in one place.

    system_prompt.md  who the model is, how it uses the course material and the tools, and
                      how it answers: TASK 1 changes it (plain text, no Python needed)
    system_prompt()   reads that file, fresh for every question
    build_prompt()    puts it together with the tool list, this question's seed, deadlines
                      and conversation

The principle: the course material makes an expert's answer specific to this course;
it does not fence the expert in. The one hard rule is for the one thing a model cannot
know by itself: facts about this course.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..index import Passage, format_passages
from ..tools import TOOLS
from .context import DEADLINE_DAYS, Context

SYSTEM_FILE = Path(__file__).with_name("system_prompt.md")  # TASK 1 (see agent/README.md)
NOTE = re.compile(r"<!--.*?-->[ \t]*\n?", re.DOTALL)  # notes for people (and their line), never sent to the model

HISTORY_TURNS = 20  # how many earlier messages the model sees
HISTORY_CHARS = 6000  # an earlier message longer than this is cut


# -------------------------------------------------------- the system prompt
def system_prompt(tools: str = "", timezone: str = "", now: str = "") -> str:
    """The system prompt, read from system_prompt.md each time (so an edit applies to the
    next question), without its <!-- notes -->, and with its three placeholders filled in.
    Plain replace, not str.format, so braces elsewhere (LaTeX, JSON) are left alone."""
    text = NOTE.sub("", SYSTEM_FILE.read_text(encoding="utf-8")).strip()
    return text.replace("{tools}", tools).replace("{timezone}", timezone).replace("{now}", now)


# ----------------------------------------------------------------- one turn
def build_prompt(question: str, context: Context, *, history: list[dict] = (),
                 tools: list[dict] | None = None, timezone: str = "America/Chicago",
                 now: datetime | None = None) -> tuple[str, str]:
    """Returns (system prompt, prompt) for one turn. `tools` are the Toolbox's specs
    (default: every tool in TOOLS).

    The prompt holds the seed, then the conversation, then the question, separated by
    blank lines (abridged):

        The course's files (open any of them with read_document and its path):
        - file: lecture1.pdf | courses/Intro to Machine Learning (101)/files/Lectures/lecture1.pdf.md

        Upcoming deadlines, the next 14 days (exact, from Canvas):
        - 2026-10-19 23:59 · Intro to Machine Learning · Homework 2: Logistic Regression (assignment, submitted)

        Sources (a first search):
        [1] Midterm moved to Oct 28 | Intro to Machine Learning | announcement | score 0.39
        file: courses/Intro to Machine Learning (101)/announcements/2026-10-01-midterm-moved-to-oct-28-7001.md
        link: https://canvas.test.edu/courses/101/discussion_topics/7001
        The midterm exam is now on October 28 in Room 210.

        Conversation so far:
        Student: When is the midterm exam?

        Assistant: The midterm is on October 28 [1].

        Student: Which room is it in?
    """
    now = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    specs = tools if tools is not None else [t.spec() for t in TOOLS]
    system = system_prompt(tools=_tool_lines(specs), timezone=timezone,
                           now=f"{now:%A, %B} {now.day}, {now:%Y %H:%M}")  # "Wednesday, October 14, 2026 10:00"

    parts = [
        _files_section(context),  # "" when there is no file list
        _deadlines_section(context.deadlines),
        _sources_section(context.passages),
        _history_section(history),  # "" for the first question of a conversation
        f"Student: {question}",
    ]
    return system, "\n\n".join(p for p in parts if p)


def _tool_lines(specs: list[dict]) -> str:
    """One line per tool for {tools}: its name and the first sentence of its description."""
    return "\n".join(f"- {s['name']}: {s['description'].split('. ')[0].rstrip('.')}." for s in specs)


def _files_section(context: Context) -> str:
    """What the model can open: the course's items with their paths, or, with "All courses"
    on the Ask page, the course names and a reminder to ask which one is meant."""
    if not context.course_files:
        return ""
    listing = "\n".join(f"- {f}" for f in context.course_files)
    if not context.course_chosen:
        return ("No course is chosen, so the sources come from all of the student's courses:\n" + listing
                + "\nIf the question is about one course and the conversation does not say which, "
                  "ask the student which course they mean before answering.")
    return "The course's files (open any of them with read_document and its path):\n" + listing


def _deadlines_section(deadlines: list[dict]) -> str:
    """The exact deadlines from facts.json, already in the student's time zone."""
    lines = [f"- {d['due_local']} · {d['course']} · {d['title']} ({d['kind']}"
             f"{', submitted' if d.get('submitted') else ''})" for d in deadlines]
    return (f"Upcoming deadlines, the next {DEADLINE_DAYS} days (exact, from Canvas):\n"
            + ("\n".join(lines) or f"- nothing is due in the next {DEADLINE_DAYS} days"))


def _sources_section(passages: list[Passage]) -> str:
    """The seed's passages, numbered [1], [2], ... so the answer can cite them."""
    return "Sources (a first search):\n" + (format_passages(passages) if passages
                                            else "(no matching course material)")


def _history_section(history: list[dict]) -> str:
    """The last HISTORY_TURNS messages, each cut at HISTORY_CHARS characters."""
    if not history:
        return ""
    turns = [f"{'Student' if t['role'] == 'student' else 'Assistant'}: {t['text'][:HISTORY_CHARS]}"
             for t in history[-HISTORY_TURNS:]]
    return "Conversation so far:\n" + "\n\n".join(turns)
