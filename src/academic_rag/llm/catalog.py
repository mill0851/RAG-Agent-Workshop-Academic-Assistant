"""The models the Ask page's dropdown offers, whether each works on this computer, and
which effort levels each accepts."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile

from ..config import Settings
from .base import EFFORTS, PROVIDERS

GEMINI_MODELS = [("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite (most free quota)"),
                 ("gemini-3.8-flash", "Gemini 3.8 Flash"), ("gemini-3.6-flash", "Gemini 3.6 Flash")]
CLAUDE_MODELS = [("", "Claude (your default model)"), ("sonnet", "Claude Sonnet"), ("opus", "Claude Opus"),
                 ("haiku", "Claude Haiku")]


def model_choices(settings: Settings) -> list[dict]:
    """Every provider and model, with whether it can run here and why not, and the effort
    levels it accepts (Gemini's thinkingLevel, Claude's --effort and Codex's reasoning effort
    all take medium and high; Codex lists them per model)."""
    choices = []

    def add(provider: str, model: str, label: str, problem: str = "", efforts=EFFORTS) -> None:
        choices.append({"provider": provider, "model": model, "label": label, "available": not problem,
                        "problem": problem, "efforts": [e for e in EFFORTS if e in efforts]})

    for model, label in GEMINI_MODELS:
        add("gemini", model, label, "" if settings.gemini_api_key else "set GEMINI_API_KEY in .env")
    for model, label in CLAUDE_MODELS:
        add("claude", model, label, "" if shutil.which("claude") else "Claude Code is not installed")
    if shutil.which("codex"):
        add("codex", "", "Codex (your default model)")
        for model, label, efforts in codex_models():
            add("codex", model, f"Codex · {label}", efforts=efforts)
    else:
        add("codex", "", "Codex", "the Codex CLI is not installed")
    configured = (settings.llm_provider, settings.llm_model)
    if all(configured) and configured[0] in PROVIDERS \
            and not any((c["provider"], c["model"]) == configured for c in choices):
        add(*configured, f"{settings.llm_provider} · {settings.llm_model} (from .env)")
    return choices


def codex_models() -> list[tuple[str, str, list[str]]]:
    """The models the installed Codex CLI lists for your account, best first, with the
    reasoning efforts each supports."""
    try:
        out = subprocess.run([shutil.which("codex"), "debug", "models"], capture_output=True, text=True,
                             encoding="utf-8", timeout=30, cwd=tempfile.gettempdir()).stdout
        models = json.loads(out).get("models", [])
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return []  # an older Codex, or no network: its default model still works
    listed = sorted((m for m in models if m.get("visibility") == "list"), key=lambda m: m.get("priority", 99))
    return [(m["slug"], m.get("display_name") or m["slug"],
             [level.get("effort") for level in m.get("supported_reasoning_levels") or []] or list(EFFORTS))
            for m in listed if m.get("slug")]
