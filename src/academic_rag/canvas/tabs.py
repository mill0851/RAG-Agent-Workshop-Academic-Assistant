"""One handler per Canvas tab. Each asks Canvas for the tab's items, turns each one into
readable text, and saves it with ctx.emit(). Course files are in files.py.

    load_modules()   GET .../modules and .../modules/:id/items, read before every tab
    modules()        the Modules tab: one item per module, listing what it contains
    home()           GET .../front_page
    syllabus()       the syllabus_body that came with the course (no extra request)
    pages()          GET .../pages, then GET .../pages/:url for each page's text
    announcements()  GET /api/v1/announcements
    discussions()    GET .../discussion_topics
    assignments()    GET .../assignments?include[]=submission
    quizzes()        GET .../quizzes
    facts()          the exact values (due dates, points, scores, grade) for facts.json

Canvas sends rich text as HTML; html_to_text() turns it into Markdown-like text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..course_files import html_to_text, read_json, slug
from .context import CourseContext, optional

# What facts.json keeps of your own submission to each assignment
SUBMISSION_FIELDS = ("workflow_state", "score", "grade", "submitted_at", "graded_at", "late", "missing", "excused")


# ------------------------------------------------------------------ modules
def load_modules(ctx: CourseContext) -> None:
    """Read the modules before anything else. A module item says which page, assignment,
    quiz, discussion or file it is, so every handler can look up its item's module, and
    the File items tell files.linked_files() which files the course shares."""
    for m in optional(lambda: ctx.client.list_modules(ctx.cid), "modules", []):
        items = optional(lambda m=m: ctx.client.list_module_items(ctx.cid, m["id"]), "module items", [])
        ctx.modules.append((m, items))
        for it in items:
            kind = (it.get("type") or "").lower()
            if kind == "page" and it.get("page_url"):  # pages are known by their address
                ctx.module_of[f"page:{it['page_url']}"] = m.get("name", "")
            elif kind in {"file", "assignment", "discussion", "quiz"} and it.get("content_id"):
                ctx.module_of[f"{kind}:{it['content_id']}"] = m.get("name", "")  # the rest by their id
                if kind == "file":
                    ctx.linked_files.add(str(it["content_id"]))


def modules(ctx: CourseContext) -> None:
    """The Modules tab: one saved item per module, its contents as an indented list."""
    for n, (m, items) in enumerate(ctx.modules, start=1):
        lines = [f"Unlocks: {m['unlock_at']}"] if m.get("unlock_at") else []
        lines += [_module_line(it) for it in items]
        name = m.get("name") or f"Module {n}"
        ctx.emit("modules", f"modules/{n:02d}-{slug(name)}.md", kind="module", canvas_id=m["id"], title=name,
                 url=f"{ctx.base}/courses/{ctx.cid}/modules#module_{m['id']}", content="\n".join(lines))


def _module_line(item: dict) -> str:
    """One module item as a line: "- [Page] Gradient Descent", "## Readings", ..."""
    indent, kind, title = "  " * int(item.get("indent") or 0), item.get("type") or "", item.get("title") or ""
    if kind == "SubHeader":
        return f"{indent}## {title}"
    if kind == "ExternalUrl":
        return f"{indent}- [Link] {title} ({item.get('external_url', '')})"
    return f"{indent}- [{kind}] {title}"


# ---------------------------------------------------------------- text tabs
def home(ctx: CourseContext) -> None:
    """The Home tab, when the course's home is a wiki page (its front page)."""
    page = optional(lambda: ctx.client.get_front_page(ctx.cid), "front page", None)
    if not page:  # the home shows something else instead: Modules, the Syllabus, ...
        return
    ctx.emit("home", "home.md", kind="home", canvas_id="front", title=page.get("title") or "Home",
             url=page.get("html_url") or f"{ctx.base}/courses/{ctx.cid}",
             content=html_to_text(page.get("body")), updated=page.get("updated_at", ""))


def syllabus(ctx: CourseContext) -> None:
    """The Syllabus tab. Its text came with the course (include[]=syllabus_body)."""
    ctx.emit("syllabus", "syllabus.md", kind="syllabus", canvas_id="main", title=f"{ctx.name} Syllabus",
             url=f"{ctx.base}/courses/{ctx.cid}/assignments/syllabus", content=ctx.syllabus_text)


def pages(ctx: CourseContext) -> None:
    """The Pages tab. The list has only titles and addresses, so each page is fetched again
    for its text."""
    for stub in ctx.client.list_pages(ctx.cid):
        if stub.get("front_page"):
            continue  # already saved as home.md
        page = optional(lambda s=stub: ctx.client.get_page(ctx.cid, s["url"]), f"page {stub.get('url')}", None)
        if not page:
            continue
        key = page.get("page_id", stub.get("page_id", stub["url"]))
        title = page.get("title") or stub.get("title", "")
        ctx.emit("pages", f"pages/{slug(title)}-{key}.md", kind="page", canvas_id=key, title=title,
                 url=page.get("html_url") or f"{ctx.base}/courses/{ctx.cid}/pages/{stub['url']}",
                 content=html_to_text(page.get("body")), updated=page.get("updated_at", ""),
                 module=ctx.module_for("page", stub["url"]))


def announcements(ctx: CourseContext) -> None:
    """The Announcements tab, every one since the course began. File names start with the
    date it was posted, so they sort in order."""
    for n in ctx.client.list_announcements(ctx.cid):
        posted = n.get("posted_at") or n.get("created_at") or ""
        title = n.get("title", "")
        ctx.emit("announcements", f"announcements/{posted[:10] or 'undated'}-{slug(title)}-{n['id']}.md",
                 kind="announcement", canvas_id=n["id"], title=title, url=n.get("html_url", ""),
                 content=html_to_text(n.get("message")), updated=posted)


def discussions(ctx: CourseContext) -> None:
    """The Discussions tab: each topic's opening post (replies are not saved)."""
    for t in ctx.client.list_discussions(ctx.cid):
        if t.get("is_announcement"):
            continue  # Canvas lists announcements here too; they are saved by announcements()
        title = t.get("title", "")
        ctx.emit("discussions", f"discussions/{slug(title)}-{t['id']}.md", kind="discussion", canvas_id=t["id"],
                 title=title, url=t.get("html_url", ""), content=html_to_text(t.get("message")),
                 updated=t.get("last_reply_at") or t.get("posted_at") or "",
                 module=ctx.module_for("discussion", t["id"]))


# -------------------------------------------------- assignments and quizzes
# These two tabs are saved twice: as text to search (the .md item), and as exact values
# for facts.json (due dates, points and your scores), which search is too fuzzy for.

def assignments(ctx: CourseContext) -> None:
    """The Assignments tab, each with your own submission (its status and score)."""
    ctx.assignments = []  # no longer None: the tab was read, so facts.json gets this list
    for a in ctx.client.list_assignments(ctx.cid):
        title = a.get("name", "")
        module = ctx.module_for("assignment", a["id"])
        kinds = [k for k in (a.get("submission_types") or []) if k != "none"]  # online_upload, ...
        ctx.emit("assignments", f"assignments/{slug(title)}-{a['id']}.md", kind="assignment", canvas_id=a["id"],
                 title=title, url=a.get("html_url", ""), content=_assignment_text(a, kinds),
                 updated=a.get("updated_at", ""), module=module)
        ctx.assignments.append(_assignment_fact(a, title, kinds, module))
    ctx.assignments.sort(key=lambda x: (x["due_at"] is None, x["due_at"] or "", x["name"]))  # undated last


def _assignment_text(a: dict, kinds: list[str]) -> str:
    """Due date, points and how to submit, then the description, then the rubric."""
    lines = []
    if a.get("due_at"):
        lines.append(f"Due: {a['due_at']}")
    if a.get("points_possible") is not None:
        lines.append(f"Points: {a['points_possible']}")
    if kinds:
        lines.append("Submission: " + ", ".join(kinds))
    parts = ["\n".join(lines), html_to_text(a.get("description")), _rubric_text(a.get("rubric"))]
    return "\n\n".join(p for p in parts if p)


def _assignment_fact(a: dict, title: str, kinds: list[str], module: str) -> dict:
    """One assignment's exact values for facts.json."""
    sub = a.get("submission") or {}
    return {
        "id": a["id"], "name": title, "due_at": a.get("due_at"), "unlock_at": a.get("unlock_at"),
        "lock_at": a.get("lock_at"), "points_possible": a.get("points_possible"),
        "submission_types": kinds, "url": a.get("html_url", ""), "module": module,
        "submission": {k: sub.get(k) for k in SUBMISSION_FIELDS} if sub else None,
    }


def _rubric_text(rubric: list[dict] | None) -> str:
    """A rubric as an indented list: each criterion, then its ratings."""
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


def quizzes(ctx: CourseContext) -> None:
    """The Quizzes tab: each quiz's rules and description (not its questions)."""
    ctx.quizzes = []  # no longer None: the tab was read, so facts.json gets this list
    for q in ctx.client.list_quizzes(ctx.cid):
        title = q.get("title", "")
        ctx.emit("quizzes", f"quizzes/{slug(title)}-{q['id']}.md", kind="quiz", canvas_id=q["id"], title=title,
                 url=q.get("html_url", ""), content=_quiz_text(q), updated=q.get("updated_at", ""),
                 module=ctx.module_for("quiz", q["id"]))
        ctx.quizzes.append({
            "id": q["id"], "title": title, "due_at": q.get("due_at"), "points_possible": q.get("points_possible"),
            "time_limit": q.get("time_limit"), "allowed_attempts": q.get("allowed_attempts"),
            "url": q.get("html_url", ""),
        })
    ctx.quizzes.sort(key=lambda x: (x["due_at"] is None, x["due_at"] or "", x["title"]))  # undated last


def _quiz_text(q: dict) -> str:
    """Due date, points, time limit, attempts and question count, then the description."""
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
    return "\n\n".join(p for p in ("\n".join(lines), html_to_text(q.get("description"))) if p)


# --------------------------------------------------------------- facts.json
def facts(ctx: CourseContext, course: dict, cdir: Path) -> dict:
    """The exact values, kept apart from the fuzzy search index: your course grade, and
    every assignment's and quiz's due date and points. Whatever could not be read this
    run (a tab that failed, a grade Canvas did not send) keeps its last saved value."""
    prev = read_json(cdir / "facts.json") or {}

    # Your grade comes with the course: include[]=total_scores adds it to your enrollment.
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
        "assignments": ctx.assignments if ctx.assignments is not None else prev.get("assignments", []),
        "quizzes": ctx.quizzes if ctx.quizzes is not None else prev.get("quizzes", []),
    }
