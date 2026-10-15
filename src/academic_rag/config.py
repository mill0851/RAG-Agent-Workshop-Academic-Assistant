"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REASONING_LEVELS = ("medium", "high")  # how hard the AI thinks before answering: the Ask page's menu


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass
class Settings:
    # --- Canvas
    canvas_base_url: str = ""
    canvas_api_token: str = field(default="", repr=False)  # never printed
    course_ids: list[str] = field(default_factory=list)
    enrollment_states: list[str] = field(default_factory=lambda: ["active"])
    max_file_bytes: int = 50 * 1024 * 1024  # Canvas files bigger than this are skipped

    # --- Your own documents (uploads): a whole textbook can be large
    max_upload_bytes: int = 200 * 1024 * 1024

    # --- Embeddings / retrieval (local CPU only)
    embedding_backend: str = "local"  # local | hash (hash is for tests/dev only)
    chunk_chars: int = 1000
    chunk_overlap: int = 150
    top_k: int = 6

    # --- Your time zone, for due dates (Canvas stores them in UTC)
    timezone: str = "America/Chicago"

    # --- The answering agent (the Ask tab)
    llm_provider: str = ""  # gemini | claude | codex; empty = the Ask tab explains how to choose one
    llm_model: str = ""  # optional; each provider has a default
    reasoning_level: str = "medium"  # medium | high: the default; the Ask page can switch per question
    gemini_api_key: str = field(default="", repr=False)  # never printed

    # --- Storage
    data_dir: Path = Path("./data")

    # --- API server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    @property
    def courses_dir(self) -> Path:
        return self.data_dir / "courses"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "embed_manifest.json"

    def require_canvas(self) -> None:
        if not self.canvas_base_url:
            raise SystemExit("CANVAS_BASE_URL is not set (see .env.example).")
        if not self.canvas_api_token:
            raise SystemExit(
                "CANVAS_API_TOKEN is not set. Create one in Canvas under "
                "Account > Settings > '+ New Access Token' and put it in .env."
            )


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build Settings from the environment. Values in .env do not override
    variables that are already exported in the shell."""
    try:
        from dotenv import load_dotenv

        if env_file:
            load_dotenv(env_file, override=False)
    except ImportError:  # python-dotenv is optional at runtime
        pass

    e = os.environ.get
    defaults = Settings()
    reasoning_level = (e("REASONING_LEVEL") or "").strip().lower() or defaults.reasoning_level
    if reasoning_level not in REASONING_LEVELS:
        raise SystemExit(f"REASONING_LEVEL must be medium or high (blank = medium), not '{reasoning_level}'.")
    return Settings(
        canvas_base_url=(e("CANVAS_BASE_URL") or "").rstrip("/"),
        canvas_api_token=e("CANVAS_API_TOKEN", ""),
        course_ids=_csv(e("CANVAS_COURSE_IDS")),
        enrollment_states=_csv(e("CANVAS_ENROLLMENT_STATES")) or ["active"],
        max_upload_bytes=int(float(e("MAX_UPLOAD_MB") or defaults.max_upload_bytes / 2**20) * 2**20),
        embedding_backend=(e("EMBEDDING_BACKEND") or "local").lower(),
        chunk_chars=int(e("CHUNK_CHARS") or defaults.chunk_chars),
        chunk_overlap=int(e("CHUNK_OVERLAP") or defaults.chunk_overlap),
        top_k=int(e("TOP_K") or defaults.top_k),
        timezone=e("TIMEZONE") or defaults.timezone,
        llm_provider=(e("LLM_PROVIDER") or "").strip().lower(),
        llm_model=(e("LLM_MODEL") or "").strip(),
        reasoning_level=reasoning_level,
        gemini_api_key=e("GEMINI_API_KEY", ""),
        data_dir=Path(e("DATA_DIR") or "./data"),
        api_host=e("API_HOST") or defaults.api_host,
        api_port=int(e("API_PORT") or defaults.api_port),
        cors_origins=_csv(e("API_CORS_ORIGINS")) or defaults.cors_origins,
    )
