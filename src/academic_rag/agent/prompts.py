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

from ..index import format_passages
from ..tools import TOOLS
from .context import DEADLINE_DAYS, Context

SYSTEM_FILE = Path(__file__).with_name("system_prompt.md")  # TASK 1 (see agent/README.md)
NOTE = re.compile(r"<!--.*?-->[ \t]*\n?", re.DOTALL)  # notes for people (and their line), never sent to the model

HISTORY_TURNS = 20  # how many earlier messages the model sees
HISTORY_CHARS = 6000  # an earlier message longer than this is cut


def system_prompt(tools: str = "", timezone: str = "", now: str = "") -> str:
    """The system prompt, read from system_prompt.md each time (so an edit applies to the
    next question), without its <!-- notes -->, and with its three placeholders filled in.
    Plain replace, not str.format, so braces elsewhere (LaTeX, JSON) are left alone."""
    text = NOTE.sub("", SYSTEM_FILE.read_text(encoding="utf-8")).strip()
    return text.replace("{tools}", tools).replace("{timezone}", timezone).replace("{now}", now)


def build_prompt(question: str, context: Context, *, history: list[dict] = (),
                 tools: list[dict] | None = None, timezone: str = "America/Chicago",
                 now: datetime | None = None) -> tuple[str, str]:
    """Returns (system prompt, prompt) for one turn. `tools` are the Toolbox's specs
    (default: every tool in TOOLS); each is named with the first sentence of its description."""
    now = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    specs = tools if tools is not None else [t.spec() for t in TOOLS]
    tool_lines = "\n".join(f"- {s['name']}: {s['description'].split('. ')[0].rstrip('.')}." for s in specs)
    system = system_prompt(tools=tool_lines, timezone=timezone, now=f"{now:%A, %B} {now.day}, {now:%Y %H:%M}")

    parts = []
    if context.course_files and not context.course_chosen:
        parts.append("No course is chosen, so the sources come from all of the student's courses:\n"
                     + "\n".join(f"- {f}" for f in context.course_files)
                     + "\nIf the question is about one course and the conversation does not say which, "
                       "ask the student which course they mean before answering.")
    elif context.course_files:
        parts.append("The course's files (open any of them with read_document and its path):\n"
                     + "\n".join(f"- {f}" for f in context.course_files))
    lines = [f"- {d['due_local']} · {d['course']} · {d['title']} ({d['kind']}"
             f"{', submitted' if d.get('submitted') else ''})" for d in context.deadlines]
    parts.append(f"Upcoming deadlines, the next {DEADLINE_DAYS} days (exact, from Canvas):\n"
                 + ("\n".join(lines) or f"- nothing is due in the next {DEADLINE_DAYS} days"))
    parts.append("Sources (a first search):\n" + (format_passages(context.passages) if context.passages
                                                  else "(no matching course material)"))
    if history:
        turns = [f"{'Student' if t['role'] == 'student' else 'Assistant'}: {t['text'][:HISTORY_CHARS]}"
                 for t in history[-HISTORY_TURNS:]]
        parts.append("Conversation so far:\n" + "\n\n".join(turns))
    parts.append(f"Student: {question}")
    return system, "\n\n".join(parts)
