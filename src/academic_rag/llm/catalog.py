"""The models the Ask page's dropdown offers, and whether each works on this computer.

The menu is short on purpose: two models per AI, each with the same two effort levels,
medium and high (REASONING_LEVEL in .env picks the default). A model set in LLM_MODEL
that is not on the list is added to it, so the .env choice always shows."""

from __future__ import annotations

import shutil

from ..config import Settings
from .base import EFFORTS, PROVIDERS

# (provider, model, label), in the menu's order. Edit this list to offer other models.
MODELS = [
    ("gemini", "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
    ("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash"),
    ("codex", "gpt-6-astra", "GPT-6-Astra"),
    ("codex", "gpt-5.6-sol", "GPT-5.6-Sol"),
    ("claude", "claude-opus-5-5", "Claude Opus 5.5"),
    ("claude", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
]


def model_choices(settings: Settings) -> list[dict]:
    """Every model in MODELS (plus the one from .env), with whether it can run here and
    why not, and the effort levels it accepts."""
    models = list(MODELS)
    configured = (settings.llm_provider, settings.llm_model)
    if all(configured) and configured[0] in PROVIDERS and not any(m[:2] == configured for m in models):
        models.append((*configured, f"{settings.llm_provider} · {settings.llm_model} (from .env)"))
    choices = []
    for provider, model, label in models:
        problem = _problem(settings, provider)
        choices.append({"provider": provider, "model": model, "label": label, "available": not problem,
                        "problem": problem, "efforts": list(EFFORTS)})
    return choices


def _problem(settings: Settings, provider: str) -> str:
    """Why this AI cannot answer on this computer, or "" when it can."""
    if provider == "gemini":
        return "" if settings.gemini_api_key else "set GEMINI_API_KEY in .env"
    if provider == "claude":
        return "" if shutil.which("claude") else "Claude Code is not installed"
    return "" if shutil.which("codex") else "the Codex CLI is not installed"
