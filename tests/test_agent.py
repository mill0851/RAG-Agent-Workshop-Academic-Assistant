"""agent/: the seed, the system prompt, a whole turn with tools, and POST /ask.
The model is a stand-in (FakeProvider in conftest.py) that calls tools like a real one."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from academic_rag import llm
from academic_rag.agent import EFFORT, Context, ask, build_prompt, gather, small_to_big
from academic_rag.agent import context as context_module
from academic_rag.canvas import CanvasClient, scrape
from academic_rag.index import HashEmbedder, Passage, embed_courses, open_store
from academic_rag.llm import ProviderError
from academic_rag.serve.api import create_app
from academic_rag.tools import Toolbox

from .conftest import BASE, TOKEN, FakeProvider

ML = "Intro to Machine Learning (101)"


def passage(n, title, text="text", path="courses/x/files/a.md"):
    return Passage(n=n, title=title, course="ML", course_id="101", type="file", location="", module="",
                   url="", path=path, score=0.5, text=text)


@pytest.fixture
def indexed(canvas, settings):
    scrape(settings, client=CanvasClient(BASE, TOKEN))
    emb = HashEmbedder()
    store = open_store(settings, emb.name)
    embed_courses(settings, store, emb)
    return settings, store, emb


def run(indexed, question, provider=None, history=(), **toolbox_args):
    settings, store, emb = indexed
    provider = provider or FakeProvider()
    box = Toolbox(settings, store, emb, **toolbox_args)
    return list(ask(question, box, provider, history=history)), provider


# -------------------------------------------------------------------- the seed
def test_short_documents_are_sent_whole_and_long_ones_in_fragments(indexed, monkeypatch):
    settings, store, emb = indexed
    hits = [passage(1, "Syllabus", "fragment", path=f"courses/{ML}/syllabus.md"),
            passage(2, "Syllabus", "another fragment", path=f"courses/{ML}/syllabus.md"),
            passage(3, "Missing", "kept as is", path="courses/x/nope.md")]
    sources = small_to_big(hits, settings)
    assert [s.n for s in sources] == [1, 2]  # the syllabus once, whole; numbered again
    assert "Late work loses 10% per day" in sources[0].text and sources[0].location == ""
    assert sources[1].text == "kept as is"

    monkeypatch.setattr(context_module, "WHOLE_DOCUMENT_CHARS", 10)  # now the syllabus counts as long
    assert [s.text for s in small_to_big(hits, settings)][:2] == ["fragment", "another fragment"]


def test_long_documents_send_the_whole_page_each_fragment_came_from(indexed, monkeypatch):
    settings, store, emb = indexed
    item = next(settings.courses_dir.rglob("lecture1.pdf.md")).relative_to(settings.data_dir).as_posix()
    monkeypatch.setattr(context_module, "WHOLE_DOCUMENT_CHARS", 10)  # the lecture counts as long
    hits = [replace(passage(1, "lecture1.pdf", "the chain rule", path=item), location="p. 1"),
            replace(passage(2, "lecture1.pdf", "layer by layer", path=item), location="p. 1"),
            replace(passage(3, "lecture1.pdf", "the L2 norm", path=item), location="p. 2"),
            passage(4, "lecture1.pdf", "no page label", path=item)]
    sources = small_to_big(hits, settings)
    assert [(s.n, s.location) for s in sources] == [(1, "p. 1"), (2, "p. 2"), (3, "")]  # page 1 once
    assert sources[0].text.startswith("Lecture 1. Backpropagation") and "layer by layer" in sources[0].text
    assert "L2 norm" in sources[1].text and "Backpropagation" not in sources[1].text
    assert sources[2].text == "no page label"  # nothing to widen it to

    monkeypatch.setattr(context_module, "WHOLE_PAGE_CHARS", 10)  # now every page is too long
    assert [s.text for s in small_to_big(hits, settings)][:2] == ["the chain rule", "layer by layer"]


def test_the_seed_has_many_sources_the_file_list_and_the_deadlines(indexed, monkeypatch):
    settings, store, emb = indexed
    asked = []
    monkeypatch.setattr(context_module, "due_soon", lambda s, days, course_id=None: asked.append((days, course_id)) or [])
    context = gather("learning rate", settings=settings, store=store, embedder=emb, course_id="101")
    assert len(context.passages) > 6  # more than the Search page shows
    assert f"file: lecture1.pdf | courses/{ML}/files/Lectures/lecture1.pdf.md" in context.course_files
    assert asked == [(context_module.DEADLINE_DAYS, "101")]  # every question, for the chosen course
    everywhere = gather("learning rate", settings=settings, store=store, embedder=emb)
    assert "course: Organic Chemistry I" in everywhere.course_files  # no course chosen: the course names


def test_follow_ups_are_searched_with_the_first_question(indexed):
    settings, store, emb = indexed
    history = [{"role": "student", "text": "When is the midterm exam?"}, {"role": "assistant", "text": "Oct 28 [1]"}]
    context = gather("and which room?", settings=settings, store=store, embedder=emb, history=history)
    assert context.passages[0].title == "Midterm moved to Oct 28"


# ------------------------------------------------------------------ the prompt
def test_the_system_prompt_adds_to_the_models_knowledge_instead_of_fencing_it():
    now = datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc)
    system, _ = build_prompt("x", Context([], [], []), now=now)
    assert "Use everything you know" in system and "not to limit it" in system
    assert "Course facts" in system and "Never guess them" in system  # the one hard rule
    assert "answer from your own knowledge anyway" in system
    assert "search_course" in system and "find_text" in system and "read_document" in system
    assert "Monday, October 12, 2026 15:30" in system and "America/Chicago" in system  # local time
    assert "How to answer:" in system and "Answer directly and completely" in system
    # every tool in the Toolbox is named, so a model never overlooks a new one (Codex did)
    new_tool = {"name": "grades", "description": "The student's grades. Use it for how they are doing."}
    system, _ = build_prompt("x", Context([], [], []), tools=[new_tool])
    assert "- grades: The student's grades." in system and "search_course" not in system


def test_the_prompt_carries_files_deadlines_sources_history_and_question():
    deadlines = [{"due_local": "2026-10-15 23:59", "course": "Physics", "title": "PS 4", "kind": "assignment",
                  "submitted": True}]
    context = Context([passage(1, "Syllabus", "Midterm: Oct 28")], deadlines, ["file: HW2.pdf | courses/x/HW2.pdf.md"])
    history = [{"role": "student", "text": "hi"}, {"role": "assistant", "text": "x" * 7000}]
    _, prompt = build_prompt("When is the midterm?", context, history=history)
    assert "- file: HW2.pdf | courses/x/HW2.pdf.md" in prompt  # the model knows what exists, and how to open it
    assert "- 2026-10-15 23:59 · Physics · PS 4 (assignment, submitted)" in prompt
    assert "[1] Syllabus" in prompt and "file: courses/x/files/a.md" in prompt and "Midterm: Oct 28" in prompt
    assert "Student: hi" in prompt and "x" * 6001 not in prompt  # long messages are cut
    assert prompt.endswith("Student: When is the midterm?")
    _, empty = build_prompt("x", Context([], [], []))
    assert "nothing is due in the next 14 days" in empty and "(no matching course material)" in empty
    # "All courses": the model knows the sources are mixed, and asks which course when it matters
    _, mixed = build_prompt("what should I study?", Context([], [], ["course: Physics", "course: Calculus"],
                                                            course_chosen=False))
    assert "No course is chosen" in mixed and "- course: Physics" in mixed and "ask the student which course" in mixed


def test_the_system_prompt_is_read_from_its_file_every_time(tmp_path, monkeypatch):
    from academic_rag.agent import prompts

    assert "<!--" not in build_prompt("x", Context([], [], []))[0]  # notes for people never reach the model
    edited = tmp_path / "system_prompt.md"
    monkeypatch.setattr(prompts, "SYSTEM_FILE", edited)
    edited.write_text("<!-- a note -->\nBe brief. Use $\\frac{a}{b}$ notation.\n<!-- a note on its own line -->\n"
                      "Tools:\n{tools}\nZone {timezone}.", encoding="utf-8")
    system, _ = build_prompt("x", Context([], [], []), tools=[{"name": "t", "description": "Does t."}])
    assert system == "Be brief. Use $\\frac{a}{b}$ notation.\nTools:\n- t: Does t.\nZone America/Chicago."
    edited.write_text("Changed.", encoding="utf-8")  # an edit applies to the next question, no restart
    assert build_prompt("x", Context([], [], []))[0] == "Changed."


def test_broad_questions_are_told_to_read_the_documents():
    system, _ = build_prompt("x", Context([], [], []))
    assert "a sample of the course, not all of it" in system and "never generic study advice" in system


# -------------------------------------------------------------------- a turn
def test_a_turn_streams_sources_tools_text_and_a_trace(indexed):
    provider = FakeProvider(tool_calls=[("find_text", {"text": "Room 210"})])
    events, provider = run(indexed, "When is the midterm exam?", provider)
    assert [e["type"] for e in events] == ["sources", "tool", "text", "text", "trace", "done"]
    assert events[0]["passages"][0]["title"] == "Midterm moved to Oct 28" and events[0]["provider"] == "fake"
    assert events[1] == {"type": "tool", "name": "find_text", "args": {"text": "Room 210"}}
    assert "Room 210" in provider.results[0]  # the tool really ran

    trace = events[4]
    system, prompt, effort = provider.calls[0]
    assert (trace["system"], trace["prompt"], trace["effort"]) == (system, prompt, EFFORT)
    assert [c["name"] for c in trace["tools"]] == ["find_text"] and trace["seconds"] >= 0


# ----------------------------------------------------------------------- API
@pytest.fixture
def ask_client(canvas, settings):
    provider = FakeProvider()
    client = TestClient(create_app(settings, embedder=HashEmbedder(), provider=provider))
    for path in ("/scrape", "/embed"):
        job = client.post(path).json()
        while client.get(f"/jobs/{job['id']}").json()["status"] not in {"done", "error"}:
            pass
    return client, provider


def lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_ask_endpoint_streams_json_lines(ask_client):
    client, provider = ask_client
    r = client.post("/ask", json={"question": "When is the midterm exam?", "course": "101"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = lines(r)
    assert [e["type"] for e in events] == ["sources", "text", "text", "trace", "done"]
    assert all(p["course_id"] == "101" for p in events[0]["passages"])
    assert provider.calls[0][2] == EFFORT and events[-2]["effort"] == EFFORT


def test_the_page_can_ask_the_model_to_think_harder(ask_client):
    client, provider = ask_client
    events = lines(client.post("/ask", json={"question": "midterm?", "effort": "high"}))
    assert provider.calls[0][2] == "high" and events[-2] == {**events[-2], "type": "trace", "effort": "high"}
    assert client.post("/ask", json={"question": "midterm?", "effort": "extreme"}).status_code == 422


def test_each_question_opens_an_mcp_session_that_closes_with_it(ask_client):
    client, provider = ask_client
    lines(client.post("/ask", json={"question": "midterm?"}))
    url = provider.mcp_urls[0]
    assert url.startswith("http://testserver/mcp/") and len(url) > len("http://testserver/mcp/") + 16
    assert client.post(url.removeprefix("http://testserver"), json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404


def test_the_mcp_endpoint_serves_a_question_in_progress(ask_client, settings):
    client, _ = ask_client
    state = client.app.state.shared
    state.sessions["abc"] = Toolbox(settings, state.store(), state.embedder(), course_id="101")
    rpc = lambda body: client.post("/mcp/abc", json={"jsonrpc": "2.0", **body})  # noqa: E731
    assert rpc({"id": 1, "method": "initialize", "params": {}}).json()["result"]["serverInfo"]["name"] == "academic-assistant"
    assert rpc({"method": "notifications/initialized"}).status_code == 202
    assert len(rpc({"id": 2, "method": "tools/list"}).json()["result"]["tools"]) >= 3
    called = rpc({"id": 3, "method": "tools/call", "params": {"name": "find_text", "arguments": {"text": "Room 210"}}})
    assert "Room 210" in called.json()["result"]["content"][0]["text"]
    assert client.get("/mcp/abc").status_code == 405


def test_ask_endpoint_reports_problems_clearly(ask_client, monkeypatch):
    client, provider = ask_client
    assert client.post("/ask", json={"question": "x", "course": "physics"}).status_code == 404
    assert client.post("/ask", json={"question": ""}).status_code == 422

    def broken(system, prompt, tools, effort):
        raise ProviderError("`codex` failed: not logged in")
        yield
    monkeypatch.setattr(provider, "answer", broken)
    events = lines(client.post("/ask", json={"question": "midterm?"}))
    assert events[0]["type"] == "sources" and events[-1] == {"type": "error", "message": "`codex` failed: not logged in"}
    assert client.app.state.shared.sessions == {}  # closed even after an error


def test_the_ask_page(ask_client):
    client, _ = ask_client
    page = client.get("/ask.html").text
    assert 'fetch("/ask"' in page and "Behind the answer" in page and "DOMPurify.sanitize" in page
    assert 'id="effort"' in page and "body.effort" in page


def test_without_a_provider_the_ask_endpoint_says_how_to_set_one_up(canvas, settings):
    client = TestClient(create_app(settings, embedder=HashEmbedder()))
    r = client.post("/ask", json={"question": "hello"})
    assert r.status_code == 400 and "LLM_PROVIDER" in r.json()["detail"]
    assert client.get("/health").json()["llm_provider"] is None


def test_the_model_list_and_picking_a_model_on_the_page(ask_client, monkeypatch):
    client, default = ask_client
    picked = []

    def fake_get_provider(settings, name=None, model=None):
        picked.append((name, model))
        p = FakeProvider(("Picked.",))
        p.name, p.model = name, model
        return p
    monkeypatch.setattr(llm, "get_provider", fake_get_provider)
    monkeypatch.setattr(llm, "model_choices", lambda settings: [
        {"provider": "codex", "model": "", "label": "Codex (your default model)", "available": True, "problem": "",
         "efforts": ["medium", "high"]}])

    body = client.get("/models").json()
    assert body["choices"][0]["label"] == "Codex (your default model)" and "default" in body
    assert body["choices"][0]["efforts"] == ["medium", "high"]  # the page's Effort menu

    events = lines(client.post("/ask", json={"question": "midterm?", "provider": "gemini", "model": "gemini-3.8-flash"}))
    assert picked == [("gemini", "gemini-3.8-flash")]
    assert (events[0]["provider"], events[0]["model"]) == ("gemini", "gemini-3.8-flash")  # shown under the answer
    assert events[1] == {"type": "text", "text": "Picked."}

    events = lines(client.post("/ask", json={"question": "midterm?"}))  # nothing picked: the .env provider
    assert events[0]["provider"] == "fake" and len(picked) == 1
    assert client.post("/ask", json={"question": "x", "provider": "mystery"}).status_code == 422
