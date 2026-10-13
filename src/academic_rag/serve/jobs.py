"""Background jobs: a sync can take minutes, longer than a browser should wait for one
request. POST /sync starts a job and returns its id; the page asks GET /jobs/{id} for
progress. One writer at a time: Chroma's embedded mode, the manifest and the course
folders must never be written by two operations at once."""

from __future__ import annotations

import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

_URL_QUERY = re.compile(r"\?[^\s'\"]*")


def clean_error(exc: BaseException) -> str:
    """Error text safe to show in a browser: no query strings (Canvas file links carry verifiers)."""
    return f"{type(exc).__name__}: {_URL_QUERY.sub('?...', str(exc))}"[:500]


class JobBusy(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    done: int = 0
    total: int = 0
    result: dict | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: str | None = None

    def _claim(self, label: str) -> None:
        with self._lock:  # a lock, because requests arrive on different threads
            if self._active:
                raise JobBusy(self._active)
            self._active = label

    def _release(self) -> None:
        with self._lock:
            self._active = None

    @contextmanager
    def exclusive(self, label: str):
        """Hold the slot for a short operation done inside the request (upload, delete)."""
        self._claim(label)
        try:
            yield
        finally:
            self._release()  # always give the slot back, even after an error

    def submit(self, kind: str, fn) -> Job:
        """Start fn(progress) in a background thread and return its Job right away."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self._claim(job.id)
        self._jobs[job.id] = job
        for old in list(self._jobs)[:-30]:  # keep the last 30
            del self._jobs[old]
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job: Job, fn) -> None:
        def progress(message: str, done: int, total: int) -> None:
            job.message, job.done, job.total = message, done, total

        job.status = "running"
        try:
            job.result = fn(progress)
            job.status = "done"
        except (Exception, SystemExit) as exc:
            job.status, job.error = "error", clean_error(exc)
        finally:
            job.finished_at = time.time()
            self._release()

    @property
    def active(self) -> str | None:
        return self._active

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return list(self._jobs.values())


def job_view(j: Job) -> dict:
    return {
        "id": j.id, "kind": j.kind, "status": j.status, "message": j.message,
        "done": j.done, "total": j.total, "result": j.result, "error": j.error,
        "started_at": j.started_at, "finished_at": j.finished_at,
    }
