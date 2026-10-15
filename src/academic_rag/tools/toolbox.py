"""The tools the agent can call, and the Toolbox that runs them for one question.

Every AI gets the same tools. Gemini's tool requests are run by our own loop
(llm/gemini.py); Claude Code and Codex reach the same Toolbox through MCP (mcp.py).
The Toolbox knows the question's course, keeps the tools inside it, and records every
call, which the Ask page shows under "Behind the answer".
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import course_files
from ..config import Settings
from ..index import Embedder, VectorStore
from .course import FIND_TEXT, READ_DOCUMENT, SEARCH_COURSE
from .tool import Tool

# The tools every question gets. TASK 2: add GRADES (from grades.py) to this list.
TOOLS: list[Tool] = [SEARCH_COURSE, FIND_TEXT, READ_DOCUMENT]

RESULT_PREVIEW = 1500  # characters of each result kept for "Behind the answer"


class Toolbox:
    """One question's tools: the course they work in, the data they read (the settings,
    the index and its embedder), and a record of every call."""

    def __init__(self, settings: Settings, store: VectorStore, embedder: Embedder, *,
                 course_id: str | None = None, tools: list[Tool] | None = None) -> None:
        self.settings, self.store, self.embedder = settings, store, embedder
        self.course_id = course_id  # None: "All courses" on the Ask page
        self.tools = {t.name: t for t in (TOOLS if tools is None else tools)}
        self.calls: list[dict] = []  # every call, for the trace
        self.mcp_url = ""  # where Claude Code and Codex reach these tools (set by the server)

    def folders(self) -> list[Path]:
        """The course folders the tools may read: the chosen course, or every course."""
        if self.course_id:
            return [d for d in [course_files.course_dir(self.settings, self.course_id)] if d]
        return course_files.CourseStore(self.settings.courses_dir).course_dirs()

    def specs(self) -> list[dict]:
        """What the model is told about each tool: name, description, input schema."""
        return [t.spec() for t in self.tools.values()]

    def call(self, name: str, args: dict | None = None) -> str:
        """Run one tool the model asked for. Problems come back as text the model can act on."""
        args = args or {}
        start = time.perf_counter()
        tool = self.tools.get(name)
        try:
            result = tool.run(self, **args) if tool else f"There is no tool called {name}."
        except TypeError as exc:  # missing or unknown arguments
            result = f"Wrong arguments for {name}: {exc}"
        except NotImplementedError:
            result = f"{name} is not built yet."
        except Exception as exc:  # a tool failing must not end the answer
            result = f"{name} failed: {type(exc).__name__}: {exc}"

        # The record that "Behind the answer" shows: a preview of the result, its size and time
        self.calls.append({"name": name, "args": args, "result": result[:RESULT_PREVIEW],
                           "chars": len(result), "ms": round((time.perf_counter() - start) * 1000)})
        return result
