"""What every model provider can do, and how one is chosen.

A provider answers one question with tools:

    answer(system, prompt, tools, effort)  yields events while the model works:
        {"type": "tool", "name": "find_text", "args": {"text": "HW 3"}}   it called a tool
        {"type": "note", "text": "I'll look for Homework 3."}             it said what it is doing
        {"type": "text", "text": "Homework 3 covers ..."}                 a piece of the answer

`tools` is the question's Toolbox (tools/toolbox.py): the same tools for every provider.
`effort` is how hard the model should think first: one of EFFORTS, the levels the Ask page offers.

Which provider answers comes from LLM_PROVIDER in .env, or from the Ask page's dropdown.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

from ..config import Settings

if TYPE_CHECKING:
    from ..tools import Toolbox

PROVIDERS = ("gemini", "claude", "codex")
EFFORTS = ("medium", "high")  # how hard the model thinks: the Ask page's Effort menu
MODEL_NAME = re.compile(r"^[\w.:\-\[\]/]{1,100}$")  # model names reach a command line: nothing else allowed


class ProviderError(RuntimeError):
    """The model could not answer (not installed, not logged in, bad key, ...)."""


class Provider(Protocol):
    name: str
    model: str

    def answer(self, system: str, prompt: str, tools: Toolbox, effort: str = "medium") -> Iterator[dict]: ...


def get_provider(settings: Settings, name: str | None = None, model: str | None = None) -> Provider:
    """The provider set in .env, or the one picked on the Ask page (name and model;
    an empty model means that provider's default)."""
    from .claude import ClaudeProvider
    from .codex import CodexProvider
    from .gemini import GeminiProvider

    if name is None:
        name, model = settings.llm_provider, settings.llm_model
    model = model or ""
    if model and not MODEL_NAME.match(model):
        raise ProviderError(f"Not a valid model name: {model!r}")
    if name == "gemini":
        if not settings.gemini_api_key:
            raise ProviderError("Gemini needs GEMINI_API_KEY in .env "
                                "(get a free key at https://aistudio.google.com/apikey).")
        return GeminiProvider(settings.gemini_api_key, model or GeminiProvider.default_model)
    if name == "claude":
        return ClaudeProvider(model)
    if name == "codex":
        return CodexProvider(model)
    raise ProviderError("No AI is set up yet. Set LLM_PROVIDER in .env to gemini, claude or codex "
                        "(see .env.example), then restart `acad serve`.")
