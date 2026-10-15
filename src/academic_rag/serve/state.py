"""What the API's routes share (State), and the shapes of their requests.

The embedder, the index and the model providers are opened on first use: loading the
embedding model takes a few seconds, and a page that only lists courses never needs it.

    State           the embedder, the index, the AIs, the jobs and the open questions
    *Request, Turn  the JSON bodies the routes accept (FastAPI checks them)
"""

from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, Field

from .. import index, llm
from ..config import Settings
from ..index import Embedder, VectorStore
from ..llm import Provider
from ..tools import Toolbox
from .jobs import JobManager


class State:
    """Everything the routes share, each part opened the first time it is needed."""

    def __init__(self, settings: Settings, embedder: Embedder | None, provider: Provider | None = None) -> None:
        self.settings = settings
        self._embedder = embedder
        self._provider = provider  # the one from .env (or a test's stand-in)
        self._picked: dict[tuple[str, str], Provider] = {}  # ones picked on the Ask page
        self._models: list[dict] | None = None
        self._store: VectorStore | None = None
        self._lock = threading.Lock()
        self.jobs = JobManager()
        self.sessions: dict[str, Toolbox] = {}  # questions in progress, by MCP session token

    def embedder(self) -> Embedder:
        """The embedding model, loaded on first use."""
        with self._lock:
            if self._embedder is None:
                self._embedder = index.get_embedder(self.settings)
            return self._embedder

    def store(self) -> VectorStore:
        """The index, opened on first use."""
        emb = self.embedder()
        with self._lock:
            if self._store is None:
                self._store = index.open_store(self.settings, emb.name)
            return self._store

    def provider(self, name: str | None = None, model: str | None = None) -> Provider:
        """The AI from .env, or the one picked on the Ask page. ProviderError if it cannot be used."""
        with self._lock:
            if name is None:
                if self._provider is None:
                    self._provider = llm.get_provider(self.settings)
                return self._provider
            key = (name, model or "")
            if key not in self._picked:
                self._picked[key] = llm.get_provider(self.settings, name, model or "")
            return self._picked[key]

    def models(self) -> list[dict]:
        """The Ask page's model menu (llm/catalog.py)."""
        with self._lock:
            if self._models is None:  # built once: what is installed does not change while the server runs
                self._models = llm.model_choices(self.settings)
            return self._models

    def embed(self, course_ids: list[str] | None = None, rebuild: bool = False, progress=None) -> index.EmbedStats:
        """Index the saved courses (all of them, or `course_ids`) with the shared index."""
        return index.embed_courses(self.settings, self.store(), self.embedder(),
                                   course_ids=course_ids, rebuild=rebuild, progress=progress)


# ----------------------------------------------------------------- requests
class ScrapeRequest(BaseModel):
    """POST /scrape: which courses, and whether to download files and prune."""

    course_ids: list[str] | None = None
    include_files: bool = True
    prune: bool = True


class EmbedRequest(BaseModel):
    """POST /embed: which courses, and whether to rebuild the index from nothing."""

    course_ids: list[str] | None = None
    rebuild: bool = False


class SyncRequest(ScrapeRequest):
    """POST /sync: a scrape's options, plus the embed's rebuild."""

    rebuild: bool = False


class SearchRequest(BaseModel):
    """POST /search: the query, an optional course and types, and how many passages."""

    query: str = Field(min_length=1)
    course: str | None = Field(None, description="Course id, or part of the course name")
    source_types: list[str] | None = None
    top_k: int = Field(6, ge=1, le=20)


class Turn(BaseModel):
    """One earlier message of the conversation."""

    role: Literal["student", "assistant"]
    text: str


class AskRequest(BaseModel):
    """POST /ask: the question, the conversation so far, and the course, AI and effort."""

    question: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list, description="Earlier messages, oldest first")
    course: str | None = Field(None, description="Course id, or part of the course name")
    provider: Literal["gemini", "claude", "codex"] | None = Field(None, description="Default: LLM_PROVIDER")
    model: str | None = Field(None, max_length=100, description="Empty: that provider's default model")
    effort: Literal["medium", "high"] | None = Field(
        None, description="How hard the model thinks (default: REASONING_LEVEL in .env, or medium)")
