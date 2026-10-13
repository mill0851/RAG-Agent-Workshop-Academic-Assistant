"""The answering agent: one question in, a streamed, cited answer out.

A turn is three steps:

    1. context.py   the seed: a first search, short documents whole, the course's
                    file list, and the upcoming deadlines
    2. prompts.py   the prompt: the rules in system_prompt.md, the seed, the conversation
    3. llm/         the model answers, calling tools (tools/toolbox.py) when the seed
                    is not enough: search again, find exact text, read whole documents

Code gathers the seed and keeps the tools inside the course; the model decides what
else to look up and how to answer.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..llm import Provider
from ..tools import Toolbox
from .context import gather
from .prompts import build_prompt

EFFORT = "medium"  # how hard the model thinks before answering, unless the Ask page picks "high"


def ask(question: str, tools: Toolbox, provider: Provider, *, history: list[dict] = (),
        effort: str = EFFORT) -> Iterator[dict]:
    """Answer one question. `tools` is this question's Toolbox: it holds the course and
    the data the tools read. Yields events for the web page:

        {"type": "sources", "passages": [...], "deadlines": [...], "provider": ..., "model": ...}
        {"type": "tool", "name": "find_text", "args": {...}}    the model looked something up
        {"type": "note", "text": "..."}                          it said what it is doing
        {"type": "text", "text": "..."}                          a piece of the answer
        {"type": "trace", "system": ..., "prompt": ..., "tools": [...], "seconds": ...}
        {"type": "done"}
    """
    started = time.perf_counter()
    settings = tools.settings

    # 1. The seed
    context = gather(question, settings=settings, store=tools.store, embedder=tools.embedder,
                     history=history, course_id=tools.course_id)
    yield {"type": "sources", "passages": [p.to_dict() for p in context.passages],
           "deadlines": context.deadlines, "provider": provider.name, "model": getattr(provider, "model", "")}

    # 2. The prompt (it names every tool in the Toolbox, so a new tool is never overlooked)
    system, prompt = build_prompt(question, context, history=history, tools=tools.specs(),
                                  timezone=settings.timezone)

    # 3. The answer: the model may call tools while it works
    yield from provider.answer(system, prompt, tools, effort)

    yield {"type": "trace", "system": system, "prompt": prompt, "tools": tools.calls, "effort": effort,
           "seconds": round(time.perf_counter() - started, 1)}
    yield {"type": "done"}
