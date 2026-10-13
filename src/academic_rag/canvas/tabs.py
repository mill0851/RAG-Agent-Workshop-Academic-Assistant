"""One function per Canvas tab. Each asks Canvas for the tab's items and saves each one
with ctx.emit(); files are in files.py. facts() collects the exact values (due dates,
points, scores) into facts.json."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..course_files import html_to_text, read_json, slug
from .context import CourseContext, optional


# ------------------------------------------------------------------ modules
def load_modules(ctx: CourseContext) -> None:
    """Read the module list first: it tells every other item which module it is in."""
    for m in optional(lambda: ctx.client.list_modules(ctx.cid), "modules", []):
        items = optional(lambda m=m: ctx.client.list_module_items(ctx.cid, m["id"]), "module items", [])
        ctx.modules.append((m, items))
        for it in items:
            kind = (it.get("type") or "").lower()
            if kind == "page" and it.get("page_url"):
                ctx.module_of[f"page:{it['page_url']}"] = m.get("name", "")
            elif kind in {"file", "assignment", "discussion", "quiz"} and it.get("content_id"):
                ctx.module_of[f"{kind}:{it['content_id']}"] = m.get("name", "")
                if kind == "file":
                    ctx.linked_files.add(str(it["content_id"]))


def modules(ctx: CourseContext) -> None:
    for n, (m, items) in enumerate(ctx.modules, start=1):
        lines = [f"Unlocks: {m['unlock_at']}"] if m.get("unlock_at") else []
        for it in items:
            indent, kind, title = "  " * int(it.get("indent") or 0), it.get("type") or "", it.get("title") or ""
            if kind == "SubHeader":
                lines.append(f"{indent}## {title}")
            elif kind == "ExternalUrl":
                lines.append(f"{indent}- [Link] {title} ({it.get('external_url', '')})")
            else:
                lines.append(f"{indent}- [{kind}] {title}")
        name = m.get("name") or f"Module {n}"
        ctx.emit("modules", f"modules/{n:02d}-{slug(name)}.md", "module", m["id"], name,
                 f"{ctx.base}/courses/{ctx.cid}/modules#module_{m['id']}", "\n".join(lines))


# ------------------------------------------------------------- simple tabs
def home(ctx: CourseContext) -> None:
    page = optional(lambda: ctx.client.get_front_page(ctx.cid), "front page", None)
    if not page:  # the course home is not a wiki page (it shows Modules, Syllabus, ...)
        return
    ctx.emit("home", "home.md", "home", "front", page.get("title") or "Home",
             page.get("html_url") or f"{ctx.base}/courses/{ctx.cid}",
             html_to_text(page.get("body")), page.get("updated_at", ""))


def syllabus(ctx: CourseContext) -> None:
    ctx.emit("syllabus", "syllabus.md", "syllabus", "main", f"{ctx.name} Syllabus",
             f"{ctx.base}/courses/{ctx.cid}/assignments/syllabus", ctx.syllabus_text)


def pages(ctx: CourseContext) -> None:
    for stub in ctx.client.list_pages(ctx.cid):
        if stub.get("front_page"):
            continue  # already saved as home.md
        page = optional(lambda s=stub: ctx.client.get_page(ctx.cid, s["url"]), f"page {stub.get('url')}", None)
        if not page:
            continue
        key = page.get("page_id", stub.get("page_id", stub["url"]))
        title = page.get("title") or stub.get("title", "")
        ctx.emit("pages", f"pages/{slug(title)}-{key}.md", "page", key, title,
                 page.get("html_url") or f"{ctx.base}/courses/{ctx.cid}/pages/{stub['url']}",
                 html_to_text(page.get("body")), page.get("updated_at", ""),
                 module=ctx.module_of.get(f"page:{stub['url']}", ""))


def announcements(ctx: CourseContext) -> None:
    for n in ctx.client.list_announcements(ctx.cid):
        posted = n.get("posted_at") or n.get("created_at") or ""
        title = n.get("title", "")
        ctx.emit("announcements", f"announcements/{posted[:10] or 'undated'}-{slug(title)}-{n['id']}.md",
                 "announcement", n["id"], title, n.get("html_url", ""),
                 html_to_text(n.get("message")), posted)


def discussions(ctx: CourseContext) -> None:
    for t in ctx.client.list_discussions(ctx.cid):
        if t.get("is_announcement"):
            continue  # these are the Announcements tab
        title = t.get("title", "")
        ctx.emit("discussions", f"discussions/{slug(title)}-{t['id']}.md", "discussion", t["id"], title,
                 t.get("html_url", ""), html_to_text(t.get("message")),
                 t.get("last_reply_at") or t.get("posted_at") or "",
                 module=ctx.module_of.get(f"discussion:{t['id']}", ""))


# ------------------------------------------------ assignments and quizzes
def assignments(ctx: CourseContext) -> None:
    ctx.assignments = []
    for a in ctx.client.list_assignments(ctx.cid):
        module = ctx.module_of.get(f"assignment:{a['id']}", "")
        lines = []
        if a.get("due_at"):
            lines.append(f"Due: {a['due_at']}")
        if a.get("points_possible") is not None:
            lines.append(f"Points: {a['points_possible']}")
        kinds = [k for k in (a.get("submission_types") or []) if k != "none"]
        if kinds:
            lines.append("Submission: " + ", ".join(kinds))
        parts = ["\n".join(lines), html_to_text(a.get("description")), _rubric_text(a.get("rubric"))]
        title = a.get("name", "")
        ctx.emit("assignments", f"assignments/{slug(title)}-{a['id']}.md", "assignment", a["id"], title,
                 a.get("html_url", ""), "\n\n".join(p for p in parts if p), a.get("updated_at", ""), module)
        sub = a.get("submission") or {}
        ctx.assignments.append({
            "id": a["id"], "name": title, "due_at": a.get("due_at"), "unlock_at": a.get("unlock_at"),
            "lock_at": a.get("lock_at"), "points_possible": a.get("points_possible"),
            "submission_types": kinds, "url": a.get("html_url", ""), "module": module,
            "submission": {k: sub.get(k) for k in (
                "workflow_state", "score", "grade", "submitted_at", "graded_at", "late", "missing", "excused"
            )} if sub else None,
        })
    ctx.assignments.sort(key=lambda x: (x["due_at"] is None, x["due_at"] or "", x["name"]))


def quizzes(ctx: CourseContext) -> None:
    ctx.quizzes = []
    for q in ctx.client.list_quizzes(ctx.cid):
        lines = []
        if q.get("due_at"):
            lines.append(f"Due: {q['due_at']}")
        if q.get("points_possible") is not None:
            lines.append(f"Points: {q['points_possible']}")
        if q.get("time_limit"):
            lines.append(f"Time limit: {q['time_limit']} minutes")
        if q.get("allowed_attempts") not in (None, 0):
            lines.append(f"Attempts allowed: {q['allowed_attempts']}")
        if q.get("question_count"):
            lines.append(f"Questions: {q['question_count']}")
        title = q.get("title", "")
        ctx.emit("quizzes", f"quizzes/{slug(title)}-{q['id']}.md", "quiz", q["id"], title,
                 q.get("html_url", ""), "\n\n".join(p for p in ("\n".join(lines), html_to_text(q.get("description"))) if p),
                 q.get("updated_at", ""), ctx.module_of.get(f"quiz:{q['id']}", ""))
        ctx.quizzes.append({
            "id": q["id"], "title": title, "due_at": q.get("due_at"), "points_possible": q.get("points_possible"),
            "time_limit": q.get("time_limit"), "allowed_attempts": q.get("allowed_attempts"),
            "url": q.get("html_url", ""),
        })
    ctx.quizzes.sort(key=lambda x: (x["due_at"] is None, x["due_at"] or "", x["title"]))


def facts(ctx: CourseContext, course: dict, cdir: Path) -> dict:
    """Exact values (dates, points, scores) kept apart from the fuzzy text index."""
    prev = read_json(cdir / "facts.json") or {}
    grade = None
    for e in course.get("enrollments") or []:
        if any(k in e for k in ("computed_current_score", "computed_current_grade")):
            grade = {"current_score": e.get("computed_current_score"), "current_grade": e.get("computed_current_grade"),
                     "final_score": e.get("computed_final_score"), "final_grade": e.get("computed_final_grade")}
            break
    return {
        "course_id": ctx.cid, "course_name": ctx.name,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grade": grade if grade is not None else prev.get("grade"),
        # lists that could not be fetched this run keep their last known values
        "assignments": ctx.assignments if ctx.assignments is not None else prev.get("assignments", []),
        "quizzes": ctx.quizzes if ctx.quizzes is not None else prev.get("quizzes", []),
    }


def _rubric_text(rubric: list[dict] | None) -> str:
    if not rubric:
        return ""
    out = ["Rubric:"]
    for c in rubric:
        line = f"- {c.get('description', '')} ({c.get('points')} pts)"
        if c.get("long_description"):
            line += ": " + " ".join(html_to_text(c["long_description"]).split())
        out.append(line)
        for r in c.get("ratings") or []:
            out.append(f"  - {r.get('points')} pts: {r.get('description', '')}")
    return "\n".join(out)
