"""Talking to language models: one file per provider, behind one interface (base.py).
Each answers a question with our tools: Gemini through our own loop, Claude Code and
Codex through MCP. See README.md in this folder."""

from .base import EFFORTS, PROVIDERS, Provider, ProviderError, get_provider
from .catalog import model_choices
