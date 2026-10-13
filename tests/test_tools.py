"""tools/: the three tools, the Toolbox that runs them, MCP, and the deadline list."""

from datetime import datetime, timezone

import pytest

from academic_rag.canvas import CanvasClient, scrape
from academic_rag.course_files import course_dir, get_course, save_upload
from academic_rag.index import HashEmbedder, embed_courses, open_store
from academic_rag.tools import TOOLS, Tool, Toolbox, due_soon, mcp

from .conftest import BASE, TOKEN

ML = "Intro to Machine Learning (101)"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # a Wednesday
NOTES = b"Week 2 notes: the logistic regression gradient is X^T (sigmoid(X w) - y)."


@pytest.fixture
def indexed(canvas, settings):
    """The fake Canvas scraped, with the student's own notes uploaded, and embedded."""
    scrape(settings, client=CanvasClient(BASE, TOKEN))
    save_upload(course_dir(settings, "101"), get_course(settings, "101"), "week 2 notes.txt", NOTES)
    emb = HashEmbedder()
    store = open_store(settings, emb.name)
    embed_courses(settings, store, emb)
    return settings, store, emb


def toolbox(indexed, **kw) -> Toolbox:
    settings, store, emb = indexed
    return Toolbox(settings, store, emb, **kw)


# ------------------------------------------------------------- the three tools
def test_every_question_gets_the_course_tools_described_for_the_model(indexed):
    specs = toolbox(indexed).specs()
    assert [s["name"] for s in specs][:3] == ["search_course", "find_text", "read_document"]  # Task 2 adds one
    for s in specs:
        assert len(s["description"]) > 80 and s["input_schema"]["type"] == "object"
    assert [t.name for t in TOOLS] == [s["name"] for s in specs]


def test_search_course_searches_by_meaning_and_shows_each_file(indexed):
    result = toolbox(indexed, course_id="101").call("search_course", {"query": "midterm exam room"})
    assert "Midterm moved to Oct 28" in result and f"file: courses/{ML}/announcements/" in result
    assert "[1]" not in result  # tool results are cited by file, not by number


def test_find_text_finds_exact_text_with_its_file_and_page(indexed):
    box = toolbox(indexed, course_id="101")
    result = box.call("find_text", {"text": "chain rule"})
    assert f"lecture1.pdf (p. 1) | file: courses/{ML}/files/Lectures/lecture1.pdf.md" in result
    assert "Backpropagation uses the chain rule" in result
    assert "Homework 2" in box.call("find_text", {"text": "homework2"})  # spaces and capitals ignored
    assert box.call("find_text", {"text": "quantum chromodynamics"}).startswith("No course text contains")


def test_read_document_reads_a_whole_item_with_its_pages(indexed):
    box = toolbox(indexed, course_id="101")
    lecture = box.call("read_document", {"path": f"courses/{ML}/files/Lectures/lecture1.pdf.md"})
    assert "(p. 1) Lecture 1" in lecture and "(p. 2) Regularization" in lecture
    assert "Regularization" in box.call("read_document", {"path": f"courses/{ML}/files/Lectures/lecture1.pdf"})


@pytest.mark.parametrize("path", ["../.env", "courses/Organic Chemistry I (102)/syllabus.md", "/etc/passwd"])
def test_read_document_stays_inside_the_chosen_course(indexed, path):
    assert toolbox(indexed, course_id="101").call("read_document", {"path": path}).startswith("That is not a document")


def test_with_no_course_chosen_the_tools_see_every_course(indexed):
    box = toolbox(indexed)
    assert "Organic Chemistry I" in box.call("read_document", {"path": "courses/Organic Chemistry I (102)/syllabus.md"})


# ----------------------------------------------------------------- the Toolbox
def test_the_toolbox_records_every_call_and_turns_problems_into_text(indexed):
    def broken(box, x: str) -> str:
        raise RuntimeError("disk on fire")

    def unbuilt(box) -> str:
        raise NotImplementedError

    box = toolbox(indexed, tools=[*TOOLS, Tool("broken", "d", {"type": "object"}, broken),
                                  Tool("unbuilt", "d", {"type": "object"}, unbuilt)])
    assert box.call("nope") == "There is no tool called nope."
    assert box.call("find_text", {"wrong": "x"}).startswith("Wrong arguments for find_text")
    assert box.call("broken", {"x": "1"}) == "broken failed: RuntimeError: disk on fire"
    assert box.call("unbuilt") == "unbuilt is not built yet."
    box.call("find_text", {"text": "chain rule"})
    last = box.calls[-1]
    assert last["name"] == "find_text" and last["args"] == {"text": "chain rule"} and last["ms"] >= 0
    assert "chain rule" in last["result"] and last["chars"] >= len(last["result"])


def test_the_tools_see_the_students_uploads_too(indexed):
    box, notes = toolbox(indexed, course_id="101"), f"courses/{ML}/uploads/week 2 notes.txt.md"
    assert notes in box.call("search_course", {"query": "logistic regression gradient notes"})
    assert notes in box.call("find_text", {"text": "sigmoid"})
    assert "sigmoid(X w) - y" in box.call("read_document", {"path": notes})


# ------------------------------------------------------------------------- MCP
def test_mcp_lists_and_calls_the_same_tools(indexed):
    box = toolbox(indexed, course_id="101")
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-03-26"}}, box)
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, box) is None

    listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, box)["result"]["tools"]
    assert [t["name"] for t in listed] == [s["name"] for s in box.specs()]  # the same tools as Gemini gets
    assert listed[0]["inputSchema"]["required"] == ["query"] and listed[0]["annotations"]["readOnlyHint"] is True

    called = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "find_text", "arguments": {"text": "chain rule"}}}, box)
    assert "Backpropagation" in called["result"]["content"][0]["text"] and box.calls[-1]["name"] == "find_text"
    assert mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, box)["error"]["code"] == -32601


# ------------------------------------------------------------- deadline list
@pytest.fixture
def scraped(canvas, settings):
    scrape(settings, client=CanvasClient(BASE, TOKEN))
    return settings


def test_due_soon_lists_the_next_days_in_local_time(scraped):
    items = due_soon(scraped, 7, now=NOW, tz="America/Chicago")
    assert [i["title"] for i in items] == ["Homework 2: Logistic Regression"]  # Quiz 1 is past, the proposal later
    hw = items[0]
    assert (hw["course_id"], hw["kind"], hw["points"], hw["submitted"]) == ("101", "assignment", 100, True)
    assert hw["due_at"] == "2026-10-20T04:59:00Z" and hw["due_local"] == "2026-10-19 23:59"
    scraped.timezone = "UTC"
    assert due_soon(scraped, 7, now=NOW)[0]["due_local"] == "2026-10-20 04:59"


def test_due_soon_soonest_first_with_quizzes_and_a_course_filter(scraped):
    month = due_soon(scraped, 30, now=NOW)
    assert [i["title"] for i in month] == ["Homework 2: Logistic Regression", "Project Proposal"]
    assert month[1]["submitted"] is False
    quiz = due_soon(scraped, 7, now=datetime(2026, 10, 8, tzinfo=timezone.utc), tz="America/Chicago")
    assert [(i["kind"], i["title"], i["due_local"]) for i in quiz] == [("quiz", "Quiz 1: Linear Models", "2026-10-10 00:00")]
    assert due_soon(scraped, 30, now=NOW, course_id="102") == []


def test_the_due_endpoint(scraped, client):
    r = client.get("/due", params={"days": 60})
    assert r.status_code == 200 and isinstance(r.json()["items"], list)
