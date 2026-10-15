import json
from pathlib import Path

import httpx
import pytest
import respx

from academic_rag.serve import cli
from academic_rag.serve.eval import Question, parse, score

from .conftest import BASE, TOKEN

EXAMPLE = Path(__file__).parent / "eval" / "questions.example.md"


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {
        "CANVAS_BASE_URL": BASE, "CANVAS_API_TOKEN": TOKEN, "EMBEDDING_BACKEND": "hash",
        "DATA_DIR": str(tmp_path / "data"), "CHUNK_CHARS": "500", "CHUNK_OVERLAP": "60",
        "CANVAS_COURSE_IDS": "",
    }.items():
        monkeypatch.setenv(k, v)


def run(*argv):
    return cli.main(["--env-file", "/nonexistent.env", *argv])


def test_check_lists_courses_without_printing_the_token(canvas, env, capsys):
    assert run("check") == 0
    out = capsys.readouterr().out
    assert "Test Student" in out and "Intro to Machine Learning" in out and TOKEN not in out


def test_bad_token_is_a_friendly_error(canvas, env, monkeypatch, capsys):
    monkeypatch.setenv("CANVAS_API_TOKEN", "nope")
    assert run("check") == 1
    err = capsys.readouterr().err
    assert "rejected the API token" in err and "nope" not in err


def test_missing_token_is_a_clear_error(env, monkeypatch):
    monkeypatch.setenv("CANVAS_API_TOKEN", "")
    with pytest.raises(SystemExit) as e:
        run("scrape")
    assert "CANVAS_API_TOKEN" in str(e.value)


def test_scrape_courses_embed_search(canvas, env, capsys, tmp_path):
    assert run("scrape") == 0
    out = capsys.readouterr().out
    assert "Intro to Machine Learning" in out and "Organic Chemistry I" in out
    assert "Announcements=1" in out and "2 course(s)" in out
    assert (tmp_path / "data" / "courses" / "Organic Chemistry I (102)" / "course.json").exists()

    assert run("courses") == 0
    out = capsys.readouterr().out
    assert "Organic Chemistry I  [CHEM 2053]" in out and "Late work loses 10% per day" in out

    assert run("embed") == 0
    assert "15 new" in capsys.readouterr().out
    assert run("courses") == 0
    out = capsys.readouterr().out
    assert "2 course(s)" in out and "15 documents indexed" in out

    assert run("search", "When", "is", "the", "midterm?", "-k", "1") == 0
    out = capsys.readouterr().out
    assert out.startswith("[1] Midterm moved to Oct 28") and "link: https://canvas.test.edu" in out

    assert run("search", "office hours", "--course", "organic", "--type", "syllabus") == 0
    assert "Room 305" in capsys.readouterr().out


def test_sync_scrapes_then_embeds(canvas, env, capsys):
    assert run("sync", "--course", "102") == 0
    out = capsys.readouterr().out
    assert "1 course(s)" in out and "1 new" in out


def test_search_before_embedding_says_what_to_run(canvas, env):
    with pytest.raises(SystemExit) as e:
        run("search", "anything")
    assert "acad scrape" in str(e.value) and "acad embed" in str(e.value)


def test_search_with_ambiguous_course_lists_choices(canvas, env, capsys):
    run("sync")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run("search", "x", "--course", "o")
    assert "Ambiguous course" in str(e.value)


def test_courses_before_scrape(env, capsys):
    assert run("courses") == 1
    assert "acad scrape" in capsys.readouterr().out


def test_changing_backend_after_indexing_is_explained(canvas, env, monkeypatch, capsys):
    run("sync")
    monkeypatch.setenv("EMBEDDING_BACKEND", "local")
    assert run("search", "midterm") == 1  # the mismatch is caught before any model is loaded
    assert "--rebuild" in capsys.readouterr().err


def test_unknown_backend_is_rejected(env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "voyage")
    with pytest.raises(SystemExit) as e:
        run("embed")
    assert "local or hash" in str(e.value)


def test_reasoning_level_is_medium_unless_set(env, monkeypatch):
    from academic_rag.config import load_settings

    monkeypatch.setenv("REASONING_LEVEL", "")
    assert load_settings(None).reasoning_level == "medium"
    monkeypatch.setenv("REASONING_LEVEL", " High ")
    assert load_settings(None).reasoning_level == "high"
    monkeypatch.setenv("REASONING_LEVEL", "extreme")
    with pytest.raises(SystemExit, match="medium or high"):
        load_settings(None)


def test_setup_reports_embedding_speed(env, capsys):
    assert run("setup") == 0
    out = capsys.readouterr().out
    assert "hash-768" in out and "chunks/sec" in out and "Downloading" not in out


def test_serve_passes_host_and_port_to_uvicorn(env, monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw, app=app))
    assert run("serve", "--port", "9123") == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9123 and seen["app"].title == "Academic Assistant API"


# --------------------------------------------------------------------- eval
def test_the_example_eval_file_parses():
    questions = parse(EXAMPLE.read_text(encoding="utf-8"))
    assert [q.text for q in questions][0] == "When is Homework 3 due?" and len(questions) == 3
    assert questions[0].course == "Physics I" and questions[0].sources == ["Problem Set 3"]
    assert questions[0].must == [["October 9", "Oct 9", "10/9"]]
    assert questions[1].must == [["10%"], ["per day"]]
    assert questions[2].must == [["kinetic energy"], ["Check yourself"]] and questions[2].must_not == []


def answer_events(text, seed=("Syllabus",), looked_up=()):
    return [{"type": "sources", "passages": [{"title": t, "path": f"courses/x/{t}.md"} for t in seed]},
            {"type": "text", "text": text},
            {"type": "trace", "tools": [{"name": "read_document", "args": {"path": f"courses/x/{t}.md"},
                                         "result": t} for t in looked_up]}]


def test_eval_scores_sources_facts_and_mistakes():
    q = Question("When is HW 3 due?", sources=["Problem Set 3"], must=[["October 9", "Oct 9"]], must_not=["Oct 2"])
    found_by_tool = score(q, answer_events("It is due Oct 9.", looked_up=["Problem Set 3"]))
    assert (found_by_tool["source"], found_by_tool["facts"], found_by_tool["passed"]) == ("tool", [True], True)
    assert found_by_tool["tools"] == 1
    in_seed = score(q, answer_events("Due October 9, not Oct 2.", seed=["Problem Set 3"]))
    assert in_seed["source"] == "seed" and in_seed["wrong"] == ["Oct 2"] and not in_seed["passed"]
    missed = score(q, answer_events("I could not find it."))
    assert (missed["source"], missed["facts"], missed["passed"]) == ("missed", [False], False)


def test_eval_asks_the_running_server_and_saves_the_results(env, tmp_path, capsys):
    questions = tmp_path / "questions.md"
    questions.write_text("## What is the late policy?\nsource: Syllabus\nmust mention: 10%\n", encoding="utf-8")
    events = answer_events("Late work loses 10% per day [1].")
    with respx.mock() as router:
        router.get("http://127.0.0.1:9123/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        asked = router.post("http://127.0.0.1:9123/ask").mock(
            return_value=httpx.Response(200, text="".join(json.dumps(e) + "\n" for e in events)))
        assert run("eval", "--file", str(questions), "--url", "http://127.0.0.1:9123", "--provider", "gemini") == 0
    assert json.loads(asked.calls[0].request.content) == {
        "question": "What is the late policy?", "provider": "gemini", "model": ""}
    out = capsys.readouterr().out
    assert "PASS  source seed" in out and "1/1 passed" in out
    saved = json.loads(next((tmp_path / "data" / "eval").glob("*-gemini.json")).read_text(encoding="utf-8"))
    assert saved["results"][0]["passed"] is True

    with respx.mock() as router:  # the same questions, thinking harder
        router.get("http://127.0.0.1:9123/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        asked = router.post("http://127.0.0.1:9123/ask").mock(
            return_value=httpx.Response(200, text="".join(json.dumps(e) + "\n" for e in events)))
        run("eval", "--file", str(questions), "--url", "http://127.0.0.1:9123", "--provider", "gemini",
            "--effort", "high")
    assert json.loads(asked.calls[0].request.content)["effort"] == "high"
    saved = json.loads(next((tmp_path / "data" / "eval").glob("*-gemini-high.json")).read_text(encoding="utf-8"))
    assert saved["effort"] == "high"


def test_eval_without_a_server_says_how_to_start_one(env, tmp_path):
    questions = tmp_path / "questions.md"
    questions.write_text("## Anything?\n", encoding="utf-8")
    with respx.mock() as router:
        router.get("http://127.0.0.1:9124/health").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(SystemExit, match="acad serve"):
            run("eval", "--file", str(questions), "--url", "http://127.0.0.1:9124")
