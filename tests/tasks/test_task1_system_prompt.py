"""Task 1: change how the agent answers, in the system prompt.

Run:  pytest tests/tasks/test_task1_system_prompt.py
Guide: src/academic_rag/agent/README.md
Edit: src/academic_rag/agent/system_prompt.md

The goal: when an answer explains a concept or a method, it ends with one short practice
question under the heading "Check yourself", so the student tests what they just read.
These tests check that the instruction is in the prompt, that it says when to skip the
question, and that nothing else was lost. The real check is the Ask page: ask for an
explanation and see whether the answer ends with the question.
"""

import json

from fastapi.testclient import TestClient

from academic_rag.agent import Context, build_prompt
from academic_rag.index import HashEmbedder
from academic_rag.serve.api import create_app

from ..conftest import FakeProvider


def system_prompt() -> str:
    return build_prompt("Explain the chain rule", Context([], [], []))[0]


def near_the_heading(radius: int = 400) -> str:
    prompt = system_prompt()
    i = prompt.find("Check yourself")
    return prompt[max(0, i - radius): i + radius].lower()


def test_the_prompt_asks_for_a_check_yourself_question():
    assert "Check yourself" in system_prompt(), (
        'give the model the exact heading, "Check yourself": then you can spot it in every answer')
    assert "question" in near_the_heading(), "say what goes under the heading: one short practice question"


def test_it_says_when_to_skip_it():
    words = near_the_heading()
    assert any(w in words for w in ("date", "deadline", "policy", "fact")), (
        "a date or a policy needs no quiz: say when to leave the question out")


def test_nothing_else_was_lost():
    prompt = system_prompt()
    for rule in ("Use everything you know", "Course facts", "Never guess them", "Cite the course material",
                 "How to answer:", "Answer directly and completely"):
        assert rule in prompt, f'"{rule}" disappeared from system_prompt.md: add to the prompt, do not replace it'


def test_every_question_gets_it(canvas, settings):
    provider = FakeProvider(("The chain rule ...",))
    client = TestClient(create_app(settings, embedder=HashEmbedder(), provider=provider))
    r = client.post("/ask", json={"question": "Explain the chain rule"})
    assert [json.loads(line)["type"] for line in r.text.splitlines()][-1] == "done"
    assert "Check yourself" in provider.calls[0][0], "the Ask page's questions use the same system_prompt.md"
