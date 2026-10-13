# tools/: what the agent can use besides the first search

Every question on the Ask page starts with a first search ([agent/](../agent/README.md)). Often
that is enough. When it is not, the model can **call tools**: search again with better words, look
up an exact name, read a whole document. This folder holds those tools and the Toolbox that runs
them.

| File | What it does |
|---|---|
| [tool.py](tool.py) | What a tool is: a function, plus the words a model reads to decide when to call it |
| [course.py](course.py) | The three tools every question gets: `search_course`, `find_text`, `read_document` |
| [toolbox.py](toolbox.py) | `TOOLS`, the list the model is offered, and the `Toolbox` that runs them for one question |
| [grades.py](grades.py) | **Task 2**: a tool you build |
| [due_soon.py](due_soon.py) | The exact deadline list every question gets |
| [mcp.py](mcp.py) | The same tools for Claude Code and Codex, over MCP |

## The idea

### Three ways to look, because one is never enough

| Tool | Finds | Good for | Bad at |
|---|---|---|---|
| `search_course(query)` | passages by **meaning** (the same search as the Search page) | "how is oxide thickness measured?" | exact codes: "HW 3" and "HW 4" look alike |
| `find_text(text)` | every line containing **exact text**, like Ctrl+F across the course | "Problem 3.5", "HW3", a theorem's name | anything phrased differently |
| `read_document(path)` | one **whole** document, with its page labels | a homework sheet, a lecture's slides | finding things: it needs a path |

A model uses them the way a person would: search, notice the right file, open it.

### A tool is a function plus a description

The model cannot read your code. It is sent each tool's name, description and input schema
([tool.py](tool.py)):

```python
FIND_TEXT = Tool(
    name="find_text",
    description=(
        "Find every line of the course material that contains this exact text (capitals and spaces are "
        "ignored), with its document, page and file path. Use it for exact names, numbers and codes that a "
        "search by meaning can miss, such as 'Problem 3.5', 'HW 3', 'Section 2.4' or a theorem's name."
    ),
    input_schema={"type": "object", "required": ["text"], "properties": {
        "text": {"type": "string", "description": "The exact text to look for"}}},
    run=find_text,
)
```

### How the model decides to call a tool

Models like Gemini, Claude and GPT are trained to reply in one of two ways: with text, or with a
request to call one of the tools they were offered. Nothing in our code decides when. The model
reads the descriptions and judges whether one would help. For *"which file has Homework 3?"*:

```
we send     the system prompt, the question, and tools: [search_course, find_text, read_document]
Gemini      {"functionCall": {"name": "find_text", "args": {"text": "HW3"}}}
we run it   find_text(box, text="HW3")  ->  "- HW3 | file: courses/ECE.../assignments/hw3-3726136.md ..."
we send     {"functionResponse": {"name": "find_text", "response": {"result": "- HW3 | file: ..."}}}
Gemini      "Homework 3 is in HW3 2026.pdf [1]."
```

So **the description is the most important line of a tool**. Too vague, and the model never calls
it. Too broad, and it calls it for everything. Say what it returns and when to use it, in the words
students use.

### One Toolbox per question

Every call goes through `Toolbox.call` in [toolbox.py](toolbox.py), so the rules live in one place:

- **Problems become text** the model can act on ("Wrong arguments ...", "There is no document at
  ..."), so a bad call never ends the answer.
- **The scope is enforced in code.** `read_document` only opens files inside the chosen course's
  folder: never `.env`, never another course. A prompt is a request; code is a guarantee.
- **Every call is recorded** for **Behind the answer**.

Gemini's tool calls are run by our own loop. Claude Code and Codex reach the same Toolbox over MCP,
the standard way to plug tools into an agent program ([llm/](../llm/README.md)). Either way the
tools, the scope and the record are the same for every AI.

**Deadlines are given, not looked up.** "What is due this week?" needs exact dates, which
`facts.json` has. The list is small, so every question gets the next 14 days of deadlines up front
([due_soon.py](due_soon.py)).

## Try it

- Ask a question on the Ask page and open **Behind the answer**: which tools did the model call,
  with which arguments, and what came back?
- Call a tool yourself, the way the model does (in `python`, with `.venv` activated):

  ```python
  >>> from academic_rag.config import load_settings
  >>> from academic_rag.index import get_embedder, open_store
  >>> from academic_rag.tools import Toolbox
  >>> s = load_settings(); e = get_embedder(s)
  >>> box = Toolbox(s, open_store(s, e.name), e, course_id="<a course id>")
  >>> print(box.call("find_text", {"text": "midterm"}))
  >>> print(box.specs())        # exactly what the model is told about the tools
  ```

## Task 2: build a tool

**You'll learn:** what makes something a tool an AI can use, and why the description matters as
much as the code.
**Time:** about 30 minutes, ~20 lines in [grades.py](grades.py) and one line in
[toolbox.py](toolbox.py). **Suits:** some Python.

### The problem

Ask *"How am I doing in this class?"* The model cannot know: your scores are not in any passage. But
Sync already saved them, exactly, in each course's `facts.json`:

```json
{
  "course_name": "Intro to Machine Learning",
  "grade": {"current_score": 91.5, "current_grade": "A-", "final_score": 88.0, "final_grade": "B+"},
  "assignments": [
    {"name": "Homework 2: Logistic Regression", "points_possible": 100,
     "submission": {"workflow_state": "graded", "score": 88.0, ...}},
    {"name": "Project Proposal", "points_possible": 20, "submission": {"workflow_state": "unsubmitted", ...}}
  ]
}
```

[grades.py](grades.py) has the tool's shape, an empty `grades()` function, and a `GRADES` tool whose
description is still `"TODO"`. It is not in `TOOLS` yet, so no model knows it exists.

### Your goal

1. **Write `grades()`.** Return a few lines of text, for example:
   ```
   Intro to Machine Learning: current score 91.5 (A-)
   - Homework 2: Logistic Regression: 88 / 100
   ```
   | Situation | Result |
   |---|---|
   | a course is chosen on the Ask page (`box.course_id`) | that course |
   | `course` names one ("organic", "ECE-5383") | that course only |
   | neither | every course |
   | `course` matches nothing | the problem, as text, so the model can try again |
   | an assignment with no score yet | left out |
2. **Write the description.** It must make a model call the tool for "how am I doing?" and "what
   is my grade in ...?", and not for "when is the midterm?".
3. **Add `GRADES` to `TOOLS`** in [toolbox.py](toolbox.py).

**Done when** `pytest tests/tasks/test_task2_grades_tool.py` passes. Then restart `acad serve`, ask
*"How am I doing in this class?"*, and open **Behind the answer**: the model called `grades`.

### Strategy

1. **Choose the courses.** `index.resolve_course(course_files.list_courses(box.settings), course)`
   turns a name into an id, or returns a problem. Otherwise use `box.course_id`, or every course.
2. **Read the facts.** `course_files.get_facts(box.settings, course_id)` returns the dict above, or
   `None`. Any value in it may be missing: a course without grades must not crash the tool.
3. **Write the lines** the way you would want to read them, and join them with newlines.

<details>
<summary>Hint 1: choosing the courses</summary>

```python
from .. import course_files, index

courses = course_files.list_courses(box.settings)
if course:
    course_id, problem = index.resolve_course(courses, course)
    if problem:
        return problem
    ids = [course_id]
else:
    ids = [box.course_id] if box.course_id else [c["id"] for c in courses]
```

</details>

<details>
<summary>Hint 2: one course's lines</summary>

```python
facts = course_files.get_facts(box.settings, course_id) or {}
grade = facts.get("grade") or {}
lines = [f"{facts.get('course_name', course_id)}: current score {grade.get('current_score')} "
         f"({grade.get('current_grade')})" if grade.get("current_score") is not None
         else f"{facts.get('course_name', course_id)}: no grade yet"]
for a in facts.get("assignments", []):
    score = (a.get("submission") or {}).get("score")
    if score is not None:
        lines.append(f"- {a['name']}: {score:g} / {a.get('points_possible'):g}")
```

</details>

<details>
<summary>Hint 3: a description that works</summary>

Say what it returns, when to call it (in the student's own words), and why nothing else will do:

```python
description=("The student's own current grade and their score on each graded assignment, exactly as "
             "Canvas has them. The course material never contains the student's scores: only this tool "
             "does. Use it whenever they ask how they are doing, what their grade or score is, or how "
             "many points they have lost. Not for due dates: those are in the deadline list.")
```

The middle sentence matters. Without it, a model that sees assignment *points* in the first search
can conclude "the scores aren't available" and never call the tool.

</details>

### Going further

- **Try a bad description.** Change it to "Returns data." and ask again. Does the model still call
  it? Try two AIs: while this tool was being built, Gemini called it from the first description,
  and Codex only once the description said the scores exist nowhere else.
- **A "what do I need on the final?" tool.** It needs the syllabus's grade weights: which numbers
  are exact, and which would the model have to read?
- **Privacy.** Grades go to the AI only when the model calls the tool. Should the Ask page say so?
