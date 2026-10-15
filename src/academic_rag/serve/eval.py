"""`acad eval`: ask the running server questions with known answers, and score them.

The questions are in a Markdown file (tests/eval/questions.md, your own; see
questions.example.md for the format):

    ## When is Homework 3 due?
    course: ECE-5383
    source: HW3 2026.pdf
    must mention: September 28 | Sep 28
    must not mention: September 21

Each question is sent to POST /ask exactly as the Ask page sends it, so the whole agent
is tested: the first search, the tools, the prompt and the model. Per question it
reports whether the expected source was found (in the first search, by a tool, or
not at all), which facts the answer mentions, how many tools were called, and the time.

    parse()   the questions file, as Questions
    ask()     one question, through POST /ask
    score()   what one answer got right
    run()     every question: print a line each, and save the results as JSON
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

FIELD = re.compile(r"^(course|source|must mention|must not mention):\s*(.*)$", re.I)  # a "name: value" line


@dataclass
class Question:
    """One question, and what its answer must and must not contain."""

    text: str
    course: str = ""
    sources: list[str] = field(default_factory=list)  # any one of these titles counts as found
    must: list[list[str]] = field(default_factory=list)  # each fact: accepted spellings
    must_not: list[str] = field(default_factory=list)


def parse(markdown: str) -> list[Question]:
    """Every '## question' heading and the 'name: value' lines under it."""
    questions: list[Question] = []
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("## "):
            questions.append(Question(line[3:].strip()))
            continue
        match = FIELD.match(line)
        if not (questions and match and match.group(2).strip()):
            continue
        q, name, value = questions[-1], match.group(1).lower(), match.group(2).strip()
        if name == "course":
            q.course = value
        elif name == "source":
            q.sources.append(value)
        elif name == "must mention":
            q.must.append([v.strip() for v in value.split("|") if v.strip()])
        else:
            q.must_not.extend(v.strip() for v in value.split("|") if v.strip())
    return questions


def score(q: Question, events: list[dict]) -> dict:
    """What one answer got right, from the events POST /ask streamed back."""
    answer = "".join(e.get("text", "") for e in events if e["type"] == "text")
    seed = " ".join(f"{p['title']} {p['path']}" for e in events if e["type"] == "sources" for p in e["passages"])
    calls = next((e["tools"] for e in events if e["type"] == "trace"), [])
    looked_up = " ".join(f"{json.dumps(c['args'])} {c['result']}" for c in calls)

    def found_in(text: str) -> bool:
        """Does this text name one of the expected sources?"""
        return any(s.lower() in text.lower() for s in q.sources)

    # Where the expected source turned up: in the first search, through a tool, or nowhere
    source = "-" if not q.sources else "seed" if found_in(seed) else "tool" if found_in(looked_up) else "missed"
    facts = [any(v.lower() in answer.lower() for v in options) for options in q.must]
    wrong = [w for w in q.must_not if w.lower() in answer.lower()]
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    return {"question": q.text, "source": source, "facts": facts, "wrong": wrong, "tools": len(calls),
            "error": error, "answer": answer,
            "passed": not error and source != "missed" and all(facts) and not wrong}


def ask(client: httpx.Client, q: Question, provider: str | None, model: str | None,
        effort: str | None = None) -> list[dict]:
    """Send one question to POST /ask, as the Ask page does, and return every event."""
    body = {"question": q.text, **({"course": q.course} if q.course else {})}
    if provider:
        body.update(provider=provider, model=model or "")
    if effort:
        body.update(effort=effort)
    with client.stream("POST", "/ask", json=body) as resp:
        if resp.status_code != 200:
            resp.read()
            return [{"type": "error", "message": f"{resp.status_code}: {resp.text[:300]}"}]
        return [json.loads(line) for line in resp.iter_lines() if line.strip()]


def run(path: Path, url: str, provider: str | None, model: str | None, out_dir: Path,
        effort: str | None = None) -> list[dict]:
    """Ask every question in the file, print a line for each and a summary, and save the
    results in out_dir (data/eval/)."""
    questions = parse(path.read_text(encoding="utf-8"))
    if not questions:
        raise SystemExit(f"No '## question' headings in {path}.")
    client = httpx.Client(base_url=url, timeout=httpx.Timeout(600.0, connect=5.0))
    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError:
        raise SystemExit(f"No server at {url}. Start it with `acad serve` (or pass --url).")

    # One line per question
    results = []
    for n, q in enumerate(questions, start=1):
        started = time.perf_counter()
        result = score(q, ask(client, q, provider, model, effort))
        result["seconds"] = round(time.perf_counter() - started, 1)
        results.append(result)
        facts = f"{sum(result['facts'])}/{len(result['facts'])}" if result["facts"] else "-"
        print(f"{n:>2}. {'PASS' if result['passed'] else 'FAIL'}  source {result['source']:<6} facts {facts:<5} "
              f"tools {result['tools']:<2} {result['seconds']:>5}s  {q.text[:60]}")
        if result["error"]:
            print(f"      error: {result['error'][:200]}")
        elif result["wrong"]:
            print(f"      mentions what it should not: {', '.join(result['wrong'])}")

    # The totals, and the results saved for comparing runs
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed · sources in the first search: "
          f"{sum(r['source'] == 'seed' for r in results)}, "
          f"found by a tool: {sum(r['source'] == 'tool' for r in results)}"
          f" · average {sum(r['seconds'] for r in results) / len(results):.1f}s")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (f"{datetime.now():%Y%m%d-%H%M%S}-{provider or 'default'}{('-' + model) if model else ''}"
            f"{('-' + effort) if effort else ''}.json")
    (out_dir / name).write_text(json.dumps({"file": str(path), "provider": provider, "model": model,
                                            "effort": effort,
                                            "questions": [asdict(q) for q in questions], "results": results},
                                           indent=2), encoding="utf-8")
    print(f"Saved {out_dir / name}")
    return results
