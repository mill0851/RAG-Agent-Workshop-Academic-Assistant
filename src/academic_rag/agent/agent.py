"""ask(): one question in, a streamed, cited answer out. Start here.

A turn is three steps, and ask() runs them in order:

    1. the seed     context.py: a first search, widened to whole pages and short
                    documents, the course's file list, and the next 14 days of deadlines
    2. the prompt   prompts.py: the rules in system_prompt.md, the tool list, the seed,
                    the conversation and the question
    3. the answer   llm/: the model answers, calling tools (tools/toolbox.py) when the
                    seed is not enough: search again, find exact text, read a document

Code decides what every question starts with, and keeps the tools inside the course.
The model decides what else to look up, and how to answer.

ask() is a generator: it yields events while it works, and the server sends each one
to the Ask page as a line of JSON (serve/agent_routes.py):

    sources   the seed's passages and deadlines, and which AI answers     after step 1
    tool      the model called a tool, with its arguments                 during step 3
    note      the model said what it is about to do (Codex does)          during step 3
    text      a piece of the answer, as it is written                     during step 3
    trace     what the model was told and looked up: "Behind the answer"  at the end
    done      the answer is complete
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..llm import Provider
from ..tools import Toolbox
from .context import gather
from .prompts import build_prompt


def ask(question: str, tools: Toolbox, provider: Provider, *, history: list[dict] = (),
        effort: str | None = None) -> Iterator[dict]:
    """Answer one question. `tools` is this question's Toolbox: it holds the course and
    the data the tools read. `history` is the conversation so far, oldest first. `effort`
    is how hard the model thinks, medium or high; None means REASONING_LEVEL from .env
    (blank there = medium). Yields events for the web page:

        {"type": "sources", "passages": [...], "deadlines": [...], "provider": ..., "model": ...}
        {"type": "tool", "name": "find_text", "args": {...}}    the model looked something up
        {"type": "note", "text": "..."}                          it said what it is doing
        {"type": "text", "text": "..."}                          a piece of the answer
        {"type": "trace", "system": ..., "prompt": ..., "tools": [...], "effort": ..., "seconds": ...}
        {"type": "done"}
    """
    started = time.perf_counter()
    settings = tools.settings
    effort = effort or settings.reasoning_level

    # 1. The seed: the page lists these sources under the answer right away
    context = gather(question, settings=settings, store=tools.store, embedder=tools.embedder,
                     history=history, course_id=tools.course_id)
    yield {"type": "sources", "passages": [p.to_dict() for p in context.passages],
           "deadlines": context.deadlines, "provider": provider.name, "model": getattr(provider, "model", "")}

    # 2. The prompt (it names every tool in the Toolbox, so a new tool is never overlooked)
    system, prompt = build_prompt(question, context, history=history, tools=tools.specs(),
                                  timezone=settings.timezone)

    # 3. The answer: the provider yields tool, note and text events while the model works.
    #    Each tool call runs through tools.call(), which records it in tools.calls.
    yield from provider.answer(system, prompt, tools, effort)

    # The trace: exactly what the model was told and what it looked up, for "Behind the answer"
    yield {"type": "trace", "system": system, "prompt": prompt, "tools": tools.calls, "effort": effort,
           "seconds": round(time.perf_counter() - started, 1)}
    yield {"type": "done"}
