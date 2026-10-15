"""HTTP API for the web pages, the answering agent and anything else that wants it.
Run with `acad serve`; interactive docs at /docs.

    api.py            create_app(): courses, files, search and the background jobs
    agent_routes.py   the Ask page and the agent: /ask, /mcp, /models, /due
    state.py          what the routes share, and the request shapes
    jobs.py           background jobs, one writer at a time

It binds to 127.0.0.1 by default and never returns the Canvas token. Long
operations (scrape, embed) run as background jobs you poll at /jobs/{id}.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, canvas, course_files, index
from ..config import Settings, load_settings
from ..course_files import SUPPORTED_EXTENSIONS
from ..index import Embedder, IndexMismatch
from ..llm import Provider
from .agent_routes import agent_router
from .jobs import JobBusy, job_view
from .jobs import clean_error as _clean_error  # noqa: F401  (tests check it here)
from .state import EmbedRequest, ScrapeRequest, SearchRequest, State, SyncRequest

STATIC_DIR = Path(__file__).parent / "static"  # the three web pages, served at /


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None,
               provider: Provider | None = None) -> FastAPI:
    """The whole app: every route below, plus the web pages. Tests pass a stand-in
    embedder and provider; `acad serve` uses the ones .env asks for."""
    settings = settings or load_settings()
    state = State(settings, embedder, provider)
    app = FastAPI(title="Academic Assistant API", version=__version__)
    app.state.shared = state  # what the routes share (tests look inside)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------- helpers
    def need_canvas() -> None:
        """Stop with 400 when .env has no Canvas address or token."""
        if not (settings.canvas_base_url and settings.canvas_api_token):
            raise HTTPException(400, "CANVAS_BASE_URL and CANVAS_API_TOKEN must be set in .env on the server.")

    def start(kind: str, fn) -> dict:
        """Start a background job; 409 if another one is running."""
        try:
            return job_view(state.jobs.submit(kind, fn))
        except JobBusy as busy:
            raise HTTPException(409, f"Another job is already running ({busy}). Wait for it to finish.")

    @contextmanager
    def exclusive(label: str):
        """Hold the one-writer lock for a short write in this request (an upload, a delete)."""
        try:
            with state.jobs.exclusive(label):
                yield
        except JobBusy as busy:
            raise HTTPException(409, f"Another job is already running ({busy}). Wait for it to finish.")

    def course_or_404(course_id: str) -> dict:
        """A saved course's details, or 404."""
        course = course_files.get_course(settings, course_id)
        if course is None:
            raise HTTPException(404, f"No course {course_id} has been scraped yet.")
        return course

    def course_path(course_id: str, path: str) -> tuple[Path, Path]:
        """(course folder, resolved target) for a path from /documents; never outside the course."""
        d = course_files.course_dir(settings, course_id)
        if d is None:
            raise HTTPException(404, "Unknown course.")
        target = (settings.data_dir / path).resolve()
        if not target.is_relative_to(d.resolve()):
            raise HTTPException(400, "Not a file inside this course.")
        return d, target

    def data_path(p: Path) -> str:
        """A path as the API shows it: relative to the data folder."""
        return p.relative_to(settings.data_dir.resolve()).as_posix()

    # ----------------------------------------------------------------- info
    @app.get("/health")
    def health() -> dict:
        """Is the server up, and what does it hold: courses, indexed items, a running job."""
        manifest = index.load_manifest(settings.manifest_path)
        return {
            "status": "ok", "version": __version__,
            "canvas_configured": bool(settings.canvas_base_url and settings.canvas_api_token),
            "canvas_base_url": settings.canvas_base_url or None,
            "embedding_backend": settings.embedding_backend, "embedder": index.embedder_name(settings),
            "courses": len(course_files.list_courses(settings)),
            "indexed_docs": len(manifest), "indexed_chunks": sum(m.get("chunks", 0) for m in manifest.values()),
            "busy_job": state.jobs.active,
            "llm_provider": settings.llm_provider or None,
        }

    # -------------------------------------------------------------- courses
    @app.get("/courses")
    def courses() -> list[dict]:
        """Every saved course, with its summary and how much of it is indexed."""
        return course_files.list_courses(settings)

    @app.get("/courses/{course_id}")
    def course(course_id: str) -> dict:
        """One saved course."""
        return course_or_404(course_id)

    @app.get("/courses/{course_id}/facts")
    def facts(course_id: str) -> dict:
        """One course's exact values: due dates, points, your scores and grade."""
        course_or_404(course_id)
        return course_files.get_facts(settings, course_id) or {}

    @app.get("/courses/{course_id}/documents")
    def documents(course_id: str,
                  type: str | None = Query(None, description="Filter by source type")) -> list[dict]:
        """One course's saved items, each with the path that downloads it."""
        course_or_404(course_id)
        return course_files.list_documents(settings, course_id, type) or []

    # -------------------------------------- files: upload, download, delete
    @app.post("/courses/{course_id}/files", status_code=201)
    def upload_file(course_id: str, file: UploadFile) -> dict:
        """Add a file (a textbook, notes, a practice exam: PDF, DOCX, PPTX, TXT, MD, HTML, CSV) to a
        course. It is searchable when this returns."""
        course = course_or_404(course_id)
        if PurePosixPath((file.filename or "").lower()).suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, "Unsupported file type. "
                                     f"Use one of: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"File is over the {settings.max_upload_bytes // 2**20} MB limit "
                                     "(MAX_UPLOAD_MB in .env).")

        # Save it and index it straight away, while no other job writes
        d = course_files.course_dir(settings, course_id)
        with exclusive("upload"):
            item, meta = course_files.save_upload(d, course, file.filename, data)
            state.embed(course_ids=[course_id])
        chunks = index.load_manifest(settings.manifest_path).get(meta["doc_id"], {}).get("chunks", 0)
        return {"path": data_path(item.resolve()), "doc_id": meta["doc_id"], "has_text": not meta["no_text"],
                "chunks": chunks}

    @app.get("/courses/{course_id}/files")
    def download_file(course_id: str,
                      path: str = Query(..., description="Path from /documents, relative to the data dir")):
        """Download a saved item: its markdown, or the original PDF/DOCX/PPTX/upload."""
        _, target = course_path(course_id, path)
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, "Not a downloadable file.")
        if not target.is_file():
            raise HTTPException(404, "File not found.")
        return FileResponse(target, filename=target.name)

    @app.delete("/courses/{course_id}/files")
    def delete_file(course_id: str, path: str = Query(..., description="Path of an uploaded file")) -> dict:
        """Delete an uploaded file (and remove it from the index). Canvas content cannot be deleted here."""
        d, target = course_path(course_id, path)
        with exclusive("delete"):
            try:
                removed = course_files.delete_upload(d, target)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if not removed:
                raise HTTPException(404, "File not found.")
            state.embed(course_ids=[course_id])
        return {"removed": [data_path(p) for p in removed]}

    # --------------------------------------------------------------- search
    @app.post("/search")
    def search(req: SearchRequest) -> dict:
        """What retrieval finds for a query, with no AI: numbered passages, best first."""
        course_id = None
        if req.course:
            course_id, problem = index.resolve_course(course_files.list_courses(settings), req.course)
            if problem:
                raise HTTPException(404, problem)
        try:
            passages = index.search(state.store(), state.embedder(), req.query, req.top_k,
                                    course_id=course_id, source_types=req.source_types)
        except IndexMismatch as exc:
            raise HTTPException(409, str(exc))
        return {"query": req.query, "passages": [p.to_dict() for p in passages]}

    # ----- the answering agent: /ask, /mcp, /models, /due (agent_routes.py)
    app.include_router(agent_router(settings, state))

    # ------------------------------------------------------ background jobs
    @app.post("/scrape", status_code=202)
    def scrape(req: ScrapeRequest | None = None) -> dict:
        """Start downloading from Canvas in the background; poll GET /jobs/{id}."""
        req = req or ScrapeRequest()
        need_canvas()

        def work(progress) -> dict:
            """The job itself, run in a background thread."""
            stats = canvas.scrape(settings, course_ids=req.course_ids, include_files=req.include_files,
                                  prune=req.prune, progress=progress)
            return {"summary": stats.summary(), "courses": [asdict(c) for c in stats.courses]}

        return start("scrape", work)

    @app.post("/embed", status_code=202)
    def embed(req: EmbedRequest | None = None) -> dict:
        """Start indexing the saved courses in the background; poll GET /jobs/{id}."""
        req = req or EmbedRequest()

        def work(progress) -> dict:
            """The job itself, run in a background thread."""
            stats = state.embed(req.course_ids, req.rebuild, progress)
            return {"summary": stats.summary(), **asdict(stats)}

        return start("embed", work)

    @app.post("/sync", status_code=202)
    def sync(req: SyncRequest | None = None) -> dict:
        """Start a scrape, then an embed, in the background: the Courses page's Sync button."""
        req = req or SyncRequest()
        need_canvas()

        def work(progress) -> dict:
            """The job itself, run in a background thread."""
            scraped = canvas.scrape(settings, course_ids=req.course_ids, include_files=req.include_files,
                                    prune=req.prune, progress=progress)
            embedded = state.embed(req.course_ids, req.rebuild, progress)
            return {"summary": f"{scraped.summary()}. {embedded.summary()}",
                    "scrape": {"courses": [asdict(c) for c in scraped.courses]}, "embed": asdict(embedded)}

        return start("sync", work)

    @app.get("/jobs")
    def jobs() -> list[dict]:
        """Every job since the server started, newest last."""
        return [job_view(j) for j in state.jobs.all()]

    @app.get("/jobs/{job_id}")
    def job(job_id: str) -> dict:
        """One job's progress, and its result when it is done."""
        j = state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job.")
        return job_view(j)

    # ---------- the web pages (mounted last, so every API route above wins)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
    return app
