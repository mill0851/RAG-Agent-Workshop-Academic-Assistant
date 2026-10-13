"""Task 2: build a tool, `grades`, that every AI can call.

Run:  pytest tests/tasks/test_task2_grades_tool.py
Guide: src/academic_rag/tools/README.md

The fake Canvas in tests/conftest.py: in Intro to Machine Learning the student's current
score is 91.5 (A-), Homework 2 was scored 88 out of 100, and the Project Proposal has no
score yet. Organic Chemistry I has no grades at all.
"""

import pytest

from academic_rag.agent import ask
from academic_rag.canvas import CanvasClient, scrape
from academic_rag.index import HashEmbedder, embed_courses, open_store
from academic_rag.tools import Toolbox, mcp
from academic_rag.tools.grades import GRADES, grades

from ..conftest import BASE, TOKEN, FakeProvider


@pytest.fixture
def indexed(canvas, settings):
    scrape(settings, client=CanvasClient(BASE, TOKEN))
    emb = HashEmbedder()
    store = open_store(settings, emb.name)
    embed_courses(settings, store, emb)
    return settings, store, emb


def toolbox(indexed, course_id=None):
    settings, store, emb = indexed
    return Toolbox(settings, store, emb, course_id=course_id)


def test_grades_reports_the_current_grade_and_each_scored_assignment(indexed):
    text = grades(toolbox(indexed, course_id="101"))
    assert "91.5" in text and "A-" in text, "start with the current score and grade from facts['grade']"
    assert "Homework 2: Logistic Regression" in text and "88" in text and "100" in text, (
        "list each assignment that has a score: its name, the score and the points possible")
    assert "Project Proposal" not in text, "the proposal has no score yet: leave it out"


def test_the_course_can_be_named_or_left_out(indexed):
    everything = grades(toolbox(indexed))
    assert "Intro to Machine Learning" in everything and "Organic Chemistry I" in everything, (
        "with no course chosen and none named, report every course")
    organic = grades(toolbox(indexed), "organic")
    assert "Organic Chemistry I" in organic and "91.5" not in organic, "a named course: only that one"
    assert "No course matches" in grades(toolbox(indexed), "astrophysics"), (
        "an unknown course name: return the problem from index.resolve_course, so the model can try again")


def test_the_description_tells_the_model_when_to_call_it():
    assert "TODO" not in GRADES.description and len(GRADES.description) > 60
    assert "grade" in GRADES.description.lower(), "say what the tool returns, in the student's words"


def test_every_ai_can_call_it(indexed):
    box = toolbox(indexed, course_id="101")
    assert "grades" in [s["name"] for s in box.specs()], "add GRADES to TOOLS in tools/toolbox.py"
    assert "91.5" in box.call("grades", {})
    listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, box)["result"]["tools"]
    assert "grades" in [t["name"] for t in listed], "Claude Code and Codex see the same tools over MCP"


def test_a_model_that_asks_for_grades_gets_them(indexed):
    provider = FakeProvider(("You are at 91.5%.",), tool_calls=[("grades", {})])
    events = list(ask("How am I doing in this class?", toolbox(indexed, course_id="101"), provider))
    trace = next(e for e in events if e["type"] == "trace")
    assert [c["name"] for c in trace["tools"]] == ["grades"] and "91.5" in trace["tools"][0]["result"]
