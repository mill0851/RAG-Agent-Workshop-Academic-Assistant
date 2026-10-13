"""What is due soon: exact deadlines from facts.json, in the student's time zone.

Search is good at meaning but cannot list "everything due this week": the dates are
spread over many items. This function reads the exact values instead. The agent gets
its list with every question (agent/context.py), the Courses page shows it in the
"Due soon" panel, and GET /due serves it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import course_files
from ..config import Settings


def due_soon(settings: Settings, days: int = 7, *, course_id: str | None = None,
             now: datetime | None = None, tz: str | None = None) -> list[dict]:
    """Everything due between `now` and `now + days` (in one course, or all), soonest
    first, as dicts like:

        {"course_id": "101", "course": "Intro to Machine Learning",
         "kind": "assignment",               # or "quiz"
         "title": "Homework 2: Logistic Regression",
         "due_at": "2026-10-20T04:59:00Z",   # UTC, exactly as Canvas gives it
         "due_local": "2026-10-19 23:59",    # in the time zone `tz`
         "points": 100,
         "submitted": True,                  # False when unknown (quizzes)
         "url": "https://canvas.../assignments/5001"}
    """
    now = now or datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    local = ZoneInfo(tz or settings.timezone)

    items = []
    for course in course_files.list_courses(settings):
        if course_id and course["id"] != str(course_id):
            continue
        facts = course_files.get_facts(settings, course["id"]) or {}
        for kind, entries in (("assignment", facts.get("assignments", [])), ("quiz", facts.get("quizzes", []))):
            for e in entries:
                if not e.get("due_at"):
                    continue  # no due date
                due = datetime.fromisoformat(e["due_at"].replace("Z", "+00:00"))
                if not now <= due < end:
                    continue
                state = (e.get("submission") or {}).get("workflow_state")
                items.append({
                    "course_id": course["id"], "course": course["name"], "kind": kind,
                    "title": e.get("name") or e.get("title", ""),
                    "due_at": e["due_at"], "due_local": due.astimezone(local).strftime("%Y-%m-%d %H:%M"),
                    "points": e.get("points_possible"),
                    "submitted": state in {"submitted", "graded", "pending_review"},
                    "url": e.get("url", ""),
                })
    # every due_at has the same UTC format, so sorting the text sorts by time
    return sorted(items, key=lambda i: i["due_at"])
