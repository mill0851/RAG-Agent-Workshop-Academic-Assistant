"""CanvasClient: every request to Canvas goes through here.

Canvas has a JSON API for everything you see in the browser. For example,
GET /api/v1/courses/41001/assignments returns that course's assignments as a list.
Your API token acts as *you*, so this client is careful with it:

- It only sends GET requests: it can read Canvas, never change it.
- It sends the token only to the Canvas host. File downloads redirect to another
  server (a CDN), which never sees the token.
- It refuses plain http://, so the token is always encrypted on the way.
- The token never appears in logs or error messages.

Three things any Canvas client has to handle:

- Pages: a list comes back at most 100 items at a time, with the address of the next
  page in the Link header. paginate() follows those links.
- Rate limits: Canvas gives each token a budget of requests. When it runs out, Canvas
  answers 429 or "403 Rate Limit Exceeded"; _request() waits and tries again.
- Errors: 401 means the token is bad, so everything stops (CanvasAuthError). 403 or
  404 means this tab or item is switched off or hidden for you, so the scraper skips
  it (CanvasNotAvailable).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

PAGE_SIZE = 100  # the most items Canvas returns per page
FIRST_WAIT, LONGEST_WAIT = 1.0, 30  # seconds before a retry: doubles after each failure, up to 30
LOW_BUDGET = 50  # when fewer requests than this are left in the budget, pause briefly

# Link: <https://canvas.../api/v1/courses?page=2&per_page=100>; rel="next"
_NEXT_PAGE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class CanvasAuthError(RuntimeError):
    """The token was rejected (401)."""


class CanvasNotAvailable(RuntimeError):
    """The resource is disabled, hidden, or not permitted for this user (403/404)."""


class CanvasClient:
    """A read-only Canvas API client, authenticated with your API token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("CANVAS_BASE_URL must use https:// so the token is encrypted in transit.")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        # follow_redirects: a file download answers with a redirect to the CDN that holds the file
        self._http = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._headers = {"Authorization": f"Bearer {token}"}

    # ---------------------------------------------------------- one request
    def _request(self, url: str, params: Any = None) -> httpx.Response:
        """GET a path like /api/v1/courses, or a full URL. Retries when there is no answer,
        when Canvas is out of request budget, and when Canvas itself fails (5xx)."""
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        # The token goes only to the Canvas host itself, never to any other server.
        headers = self._headers if urlparse(url).netloc == urlparse(self.base_url).netloc else {}

        wait = FIRST_WAIT
        for attempt in range(self.max_retries + 1):
            last_try = attempt == self.max_retries
            try:
                resp = self._http.get(url, params=params, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # No answer at all (no network, a timeout): wait, then try again.
                if last_try:
                    raise RuntimeError(f"Canvas request failed: {type(exc).__name__}") from None
                time.sleep(wait)
                wait = min(wait * 2, LONGEST_WAIT)
                continue

            if _throttled(resp) or resp.status_code >= 500:
                # Out of request budget, or a Canvas server error: wait, then try again.
                if last_try:
                    resp.raise_for_status()
                pause = _retry_after(resp, default=wait)
                log.warning("Canvas %s; retrying in %.0fs", resp.status_code, pause)
                time.sleep(pause)
                wait = min(wait * 2, LONGEST_WAIT)
                continue

            if resp.status_code == 401:
                raise CanvasAuthError(
                    "Canvas rejected the API token (401). It may be mistyped, expired, or revoked."
                )
            if resp.status_code in (403, 404):
                raise CanvasNotAvailable(f"{resp.status_code} for {urlparse(url).path}")
            resp.raise_for_status()  # any other error status
            _pause_if_budget_low(resp)
            return resp
        raise RuntimeError("unreachable")  # every pass of the loop returns, raises or retries

    def get_json(self, path: str, params: Any = None) -> Any:
        """One request, one JSON answer (a single course, page or file)."""
        return self._request(path, params).json()

    def paginate(self, path: str, params: Any = None) -> Iterator[Any]:
        """Every item of a list, across all of its pages."""
        params = _with_default(params, "per_page", PAGE_SIZE)
        url: str | None = path
        while url:
            resp = self._request(url, params)
            data = resp.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
            match = _NEXT_PAGE.search(resp.headers.get("Link", ""))
            url = match.group(1) if match else None
            params = None  # the next page's URL already carries the whole query string

    def download(self, url: str, max_bytes: int) -> bytes | None:
        """A file's bytes, or None if it is larger than max_bytes."""
        resp = self._request(url)
        if len(resp.content) > max_bytes:
            return None
        return resp.content

    # ---------------------------------------- the Canvas API endpoints used
    # Each method is one endpoint of the Canvas REST API (https://canvas.instructure.com/doc/api/).

    # Extra fields to include with each course:
    #   term                the term's name ("Fall 2026")
    #   syllabus_body       the Syllabus tab's HTML
    #   public_description  the instructor's short course description
    #   total_scores        your current and final score and grade, inside "enrollments"
    _COURSE_INCLUDES = [
        ("include[]", "term"),
        ("include[]", "syllabus_body"),
        ("include[]", "public_description"),
        ("include[]", "total_scores"),
    ]

    def get_self(self) -> dict:
        """GET /api/v1/users/self: who the token belongs to. A cheap way to test the token."""
        return self.get_json("/api/v1/users/self")

    def list_courses(self, enrollment_states: list[str]) -> list[dict]:
        """GET /api/v1/courses: your courses. Canvas takes one enrollment_state per request,
        so this asks once per state and merges the answers."""
        seen: dict[int, dict] = {}
        for state in enrollment_states or ["active"]:
            params = [*self._COURSE_INCLUDES, ("enrollment_state", state)]
            for c in self.paginate("/api/v1/courses", params):
                # A course closed by its dates comes back without a name, and cannot be read.
                if c.get("name") and c["id"] not in seen:
                    seen[c["id"]] = c
        return list(seen.values())

    def get_course(self, course_id: str) -> dict:
        """GET /api/v1/courses/:id: one course, with the same extra fields."""
        return self.get_json(f"/api/v1/courses/{course_id}", self._COURSE_INCLUDES)

    def list_tabs(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/tabs: the tabs this course shows in its menu."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/tabs"))

    def get_front_page(self, course_id: str) -> dict:
        """GET /api/v1/courses/:id/front_page: the Home page, when it is a wiki page."""
        return self.get_json(f"/api/v1/courses/{course_id}/front_page")

    def list_modules(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/modules: the modules (weeks, units), in order."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/modules"))

    def list_module_items(self, course_id: str, module_id: str | int) -> list[dict]:
        """GET /api/v1/courses/:id/modules/:module_id/items: what one module contains."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/modules/{module_id}/items"))

    def list_pages(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/pages: every page's title and address, without its text."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/pages"))

    def get_page(self, course_id: str, page_url: str) -> dict:
        """GET /api/v1/courses/:id/pages/:url: one page, with its text (body)."""
        return self.get_json(f"/api/v1/courses/{course_id}/pages/{page_url}")

    def list_assignments(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/assignments, with your own submission: its status and score."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/assignments", [("include[]", "submission")]))

    def list_quizzes(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/quizzes: the quizzes (classic Quizzes)."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/quizzes"))

    def list_announcements(self, course_id: str) -> list[dict]:
        """GET /api/v1/announcements for one course. Without a start_date, Canvas returns
        only the last 14 days, so this asks for all of them."""
        params = [
            ("context_codes[]", f"course_{course_id}"),
            ("start_date", "2000-01-01"),
            ("end_date", "2100-01-01"),
        ]
        return list(self.paginate("/api/v1/announcements", params))

    def list_discussions(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/discussion_topics (announcements are listed here too)."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/discussion_topics"))

    def list_folders(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/folders: the Files tab's folders, by id and full path."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/folders"))

    def list_files(self, course_id: str) -> list[dict]:
        """GET /api/v1/courses/:id/files: every file in the Files tab, with its download URL."""
        return list(self.paginate(f"/api/v1/courses/{course_id}/files"))

    def get_file(self, course_id: str, file_id: str | int) -> dict:
        """GET /api/v1/courses/:id/files/:file_id: one file. This works for a file the course
        links to even when its Files tab is hidden."""
        return self.get_json(f"/api/v1/courses/{course_id}/files/{file_id}")


# ------------------------------------------------------------------ helpers
def _throttled(resp: httpx.Response) -> bool:
    """Canvas says "out of request budget" with 429, or with 403 and "Rate Limit Exceeded"."""
    return resp.status_code == 429 or (resp.status_code == 403 and "rate limit" in resp.text.lower())


def _retry_after(resp: httpx.Response, default: float) -> float:
    """The seconds Canvas asks us to wait (the Retry-After header), else `default`."""
    value = resp.headers.get("Retry-After")
    return float(value) if value and value.isdigit() else default


def _pause_if_budget_low(resp: httpx.Response) -> None:
    """Canvas refills each token's budget over time, and reports what is left in the
    X-Rate-Limit-Remaining header. When it is nearly empty, a short pause avoids a 403."""
    remaining = resp.headers.get("X-Rate-Limit-Remaining")
    try:
        if remaining and float(remaining) < LOW_BUDGET:
            time.sleep(0.5)
    except ValueError:
        pass


def _with_default(params: Any, key: str, value: Any) -> Any:
    """Add key=value to the query parameters, unless they already set it. Parameters are a
    dict, or a list of (key, value) pairs when a key repeats (include[]=a&include[]=b)."""
    if params is None:
        return {key: value}
    if isinstance(params, dict):
        return {key: value, **params}
    params = list(params)
    if not any(k == key for k, _ in params):
        params.append((key, value))
    return params
