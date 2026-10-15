"""Claude Code in print mode, `claude -p`, using your Claude login.

Claude Code is an agent program: it runs the tool loop itself. Its built-in tools are
switched off (--tools ""), and it reaches our tools over MCP (tools/mcp.py) at
tools.mcp_url, so our tools are the only thing it can use. MCP tools are named
mcp__<server>__<tool> inside Claude Code: mcp__acad__search_course, ...

The system prompt and the MCP settings go in files: on Windows a long, multi-line
argument does not survive `claude.cmd`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .base import ProviderError
from .cli import run_cli

if TYPE_CHECKING:
    from ..tools import Toolbox

PREFIX = "mcp__acad__"  # how Claude Code names our tools: mcp__acad__find_text


class ClaudeProvider:
    """Answers with Claude Code, one `claude -p` run per question."""

    name = "claude"

    def __init__(self, model: str = "") -> None:
        self.model = model  # "": your Claude Code default model

    def answer(self, system: str, prompt: str, tools: Toolbox, effort: str = "medium") -> Iterator[dict]:
        """Run Claude Code once with our tools only, and turn what it prints into events."""
        mcp = {"mcpServers": {"acad": {"type": "http", "url": tools.mcp_url}}}
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--tools", "", "--mcp-config", "mcp.json", "--strict-mcp-config", "--allowedTools", "mcp__acad",
                "--effort", effort, "--system-prompt-file", "system.txt",
                *(["--model", self.model] if self.model else [])]
        files = {"system.txt": system, "mcp.json": json.dumps(mcp)}
        streamed = False

        # ENABLE_TOOL_SEARCH=false: all of our tools are loaded up front, none deferred
        for event in run_cli("claude", args, prompt, files=files, env={"ENABLE_TOOL_SEARCH": "false"}):
            kind = event.get("type")
            if kind == "stream_event":  # a piece of text, as it is written
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    streamed = True
                    yield {"type": "text", "text": delta["text"]}
            elif kind == "assistant":  # a finished message: look for the tools it called
                for block in (event.get("message") or {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = str(block.get("name", "")).removeprefix(PREFIX)
                        yield {"type": "tool", "name": name, "args": block.get("input") or {}}
            elif kind == "result":
                if event.get("is_error"):
                    raise ProviderError(f"`claude` failed: {event.get('result') or 'unknown error'}")
                if not streamed and event.get("result"):  # no partial messages: send the whole answer
                    yield {"type": "text", "text": event["result"]}
