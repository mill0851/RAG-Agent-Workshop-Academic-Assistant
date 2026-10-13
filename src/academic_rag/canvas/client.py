"""A thin, read-only Canvas REST client authenticated with your API token.

Only GET requests are issued. The token is sent as a Bearer header and is never
logged, included in exception text, or sent to any host other than Canvas itself
(file downloads bounce to a CDN, which must not see it).
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


_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class CanvasAuthError(RuntimeError):
    """The token was rejected (401)."""


class CanvasNotAvailable(RuntimeError):
    """The resource is disabled, hidden, or not permitted for this user (403/404)."""


class CanvasClient:
    """Thin, read-only Canvas REST client. Only GET requests are issued. The token
    is sent as a Bearer header and is never logged, included in exception text, or
    sent to any host other than Canvas itself (file downloads bounce to a CDN)."""

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
        self._http = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _request(self, url: str, params: Any = None) -> httpx.Response:
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        # Only attach the token when talking to the Canvas host itself.
        headers = self._headers if urlparse(url).netloc == urlparse(self.base_url).netloc else {}

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.get(url, params=params, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise RuntimeError(f"Canvas request failed: {type(exc).__name__}") from None
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            # Canvas signals throttling with 403 + "Rate Limit Exceeded" as well as 429.
            throttled = resp.status_code == 429 or (
                resp.status_code == 403 and "rate limit" in resp.text.lower()
            )
            if throttled or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                log.warning("Canvas %s; retrying in %.0fs", resp.status_code, wait)
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue

            if resp.status_code == 401:
                raise CanvasAuthError(
                    "Canvas rejected the API token (401). It may be mistyped, expired, or revoked."
                )
            if resp.status_code in (403, 404):
                raise CanvasNotAvailable(f"{resp.status_code} for {urlparse(url).path}")
            resp.raise_for_status()

            # Canvas uses a leaky bucket; back off a little when it is nearly empty.
            remaining = resp.headers.get("X-Rate-Limit-Remaining")
            if remaining:
                try:
                    if float(remaining) < 50:
                        time.sleep(0.5)
                except ValueError:
                    pass
            return resp
        raise RuntimeError("unreachable")

    def get_json(self, path: str, params: Any = None) -> Any:
        return self._request(path, params).json()

    def paginate(self, path: str, params: Any = None) -> Iterator[Any]:
        """Yield every item across all pages, following Link: rel="next"."""
        params = _with_default(params, "per_page", 100)
        url: str | None = path
        while url:
            resp = self._request(url, params)
            data = resp.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
            match = _NEXT_RE.search(resp.headers.get("Link", ""))
            url = match.group(1) if match else None
            params = None  # the next URL already carries the full query string

    def download(self, url: str, max_bytes: int) -> bytes | None:
        """Download a file; returns None if it exceeds max_bytes."""
        resp = self._request(url)
        if len(resp.content) > max_bytes:
            return None
        return resp.content

    # --- endpoints
    _COURSE_INCLUDES = [
        ("include[]", "term"),
        ("include[]", "syllabus_body"),
        ("include[]", "public_description"),
        ("include[]", "total_scores"),
    ]

    def get_self(self) -> dict:
        """Who does this token belong to? Cheap way to validate the token."""
        return self.get_json("/api/v1/users/self")

    def list_courses(self, enrollment_states: list[str]) -> list[dict]:
        # Canvas accepts one enrollment_state per request, so ask once per state.
        seen: dict[int, dict] = {}
        for state in enrollment_states or ["active"]:
            params = [*self._COURSE_INCLUDES, ("enrollment_state", state)]
            for c in self.paginate("/api/v1/courses", params):
                # Courses restricted by date come back without a name; they cannot be read.
                if c.get("name") and c["id"] not in seen:
                    seen[c["id"]] = c
        return list(seen.values())

    def get_course(self, course_id: str) -> dict:
        return self.get_json(f"/api/v1/courses/{course_id}", self._COURSE_INCLUDES)

    def list_tabs(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/tabs"))

    def get_front_page(self, course_id: str) -> dict:
        return self.get_json(f"/api/v1/courses/{course_id}/front_page")

    def list_modules(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/modules"))

    def list_module_items(self, course_id: str, module_id: str | int) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/modules/{module_id}/items"))

    def list_pages(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/pages"))

    def get_page(self, course_id: str, page_url: str) -> dict:
        return self.get_json(f"/api/v1/courses/{course_id}/pages/{page_url}")

    def list_assignments(self, course_id: str) -> list[dict]:
        # include submission => the student's own score/status for each assignment
        return list(self.paginate(f"/api/v1/courses/{course_id}/assignments", [("include[]", "submission")]))

    def list_quizzes(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/quizzes"))

    def list_announcements(self, course_id: str) -> list[dict]:
        # Without start_date Canvas only returns the last 14 days.
        params = [
            ("context_codes[]", f"course_{course_id}"),
            ("start_date", "2000-01-01"),
            ("end_date", "2100-01-01"),
        ]
        return list(self.paginate("/api/v1/announcements", params))

    def list_discussions(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/discussion_topics"))

    def list_folders(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/folders"))

    def list_files(self, course_id: str) -> list[dict]:
        return list(self.paginate(f"/api/v1/courses/{course_id}/files"))

    def get_file(self, course_id: str, file_id: str | int) -> dict:
        # works for a file the course links to, even when its Files tab is hidden
        return self.get_json(f"/api/v1/courses/{course_id}/files/{file_id}")


def _with_default(params: Any, key: str, value: Any) -> Any:
    if params is None:
        return {key: value}
    if isinstance(params, dict):
        return {key: value, **params}
    params = list(params)
    if not any(k == key for k, _ in params):
        params.append((key, value))
    return params
