"""The Codex CLI, `codex exec`, using your ChatGPT login.

Codex is an agent program: it runs the tool loop itself. It reaches our tools over MCP
(tools/mcp.py) at tools.mcp_url. Its own shell is switched off and it runs in an empty
folder in Codex's read-only sandbox, so our tools are the only thing it can use.

Codex has no system-prompt flag, so the instructions go first in the prompt, and it
sends each message whole rather than word by word.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from .base import ProviderError
from .cli import run_cli

if TYPE_CHECKING:
    from ..tools import Toolbox

BASE = ["exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
        "--disable", "shell_tool", "--disable", "unified_exec"]


class CodexProvider:
    name = "codex"

    def __init__(self, model: str = "") -> None:
        self.model = model

    def answer(self, system: str, prompt: str, tools: Toolbox, effort: str = "medium") -> Iterator[dict]:
        args = [*BASE, "-c", f"model_reasoning_effort={effort}",
                "-c", f'mcp_servers.acad.url="{tools.mcp_url}"',
                *(["--model", self.model] if self.model else []), "-"]  # "-": the prompt comes on stdin
        last = None  # Codex may write short updates before its answer: only the last message is the answer
        for event in run_cli("codex", args, f"{system}\n\n{prompt}"):
            kind, item = event.get("type"), event.get("item") or {}
            if kind == "item.started" and item.get("type") == "mcp_tool_call":
                if last:  # a message before a tool call says what it is about to do
                    yield {"type": "note", "text": last}
                    last = None
                yield {"type": "tool", "name": item.get("tool", ""), "args": item.get("arguments") or {}}
            elif kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
                if last:
                    yield {"type": "note", "text": last}
                last = item["text"]
            elif kind in {"error", "turn.failed"}:
                error = event.get("error") or {}
                raise ProviderError(f"`codex` failed: {event.get('message') or error.get('message') or kind}")
        yield {"type": "text", "text": last or ""}
