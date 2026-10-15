# Build your first RAG agent: a study assistant for your Canvas courses

This project turns your own Canvas courses into an AI study assistant. It downloads your course
material, makes it searchable on your own computer, and lets an AI answer questions about your
classes, citing where each fact came from.

## Overview

**RAG** stands for *retrieval-augmented generation*. A language model knows a lot about physics or
calculus, but nothing about *your* course: when the midterm is, how your instructor writes a
formula, what Homework 3 asks. RAG fixes that in two steps:

1. **Retrieve:** find the passages of your course material that matter for the question.
2. **Generate:** give them to the model with the question, and let it answer from them, with citations.

An **agent** adds a third step. When the first passages are not enough, the model calls **tools**
to look further by itself: search again, find an exact name, open a whole document. It decides
when it knows enough to answer.

```
 SYNC   Canvas ──► readable text ──► small chunks ──► vectors (numbers that capture meaning)
                                                          │
 ASK    "When is the midterm?" ──► nearest chunks ◄───────┘                    1. RETRIEVE
                                        │
                                        ▼
        system prompt + passages + question ──► AI ──► cited answer            2. GENERATE
                                                 ▲ │
                       tools: search again,      │ │                           3. AGENT
                       find text, read a document└─┘
```

The app has three pages: **Courses** (sync with Canvas, add your own documents), **Search** (what
retrieval finds, no AI) and **Ask** (the agent's answers, with everything it looked up).

**Today:** set it up on your laptop with your own courses, then pick a task.

| Task | What you change | Suits |
|---|---|---|
| 1. Change how the agent answers | the system prompt, in plain English | everyone: no coding needed |
| 2. Build a tool | a Python function the AI can call | some Python |
| 3. Improve search | how search results are ranked | comfortable with Python |

## Get it running

### Before the workshop

- [ ] **Python 3.14** from https://www.python.org/downloads/release/python-3148/ (scroll to
  *Files*; on Windows, tick *Add python.exe to PATH*). **Not 3.15:** it is too new for one of the
  libraries. 3.11, 3.12 and 3.13 work too. Check it: `py -3.14 --version` (macOS:
  `python3.14 --version`).
- [ ] **Git** from https://git-scm.com/downloads, and a code editor such as
  [VS Code](https://code.visualstudio.com).
- [ ] **A Canvas access token.** In Canvas: Account > Settings > **+ New Access Token**. Give it an
  expiry date after the workshop, and copy it somewhere private: Canvas shows it only once. 
- [ ] **A free Gemini API key** from https://aistudio.google.com/apikey (sign in with Google, then
  **Create API key**).

### 1. Get the code and install it

```bash
git clone https://github.com/YOUR-ORG/academic-assistant.git
cd academic-assistant
py -3.14 -m venv .venv              # macOS/Linux: python3.14 -m venv .venv
.venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Your prompt now starts with `(.venv)`. Every `acad` command needs that: in a new terminal, `cd` to
the folder and run the `activate` line again.

### 2. Add your keys

Copy `.env.example` to a new file named `.env` (`copy .env.example .env` on Windows, `cp` on
macOS/Linux). Open `.env` and fill in the values at the top:

```bash
CANVAS_BASE_URL=https://canvas.example.edu   # your school's Canvas address, as in your browser
CANVAS_API_TOKEN=...                         # the token you made
LLM_PROVIDER=gemini                          # already set
GEMINI_API_KEY=...                           # your free key
```

<details>
<summary><b>Already pay for Claude or ChatGPT?</b> Use Claude Code or Codex instead of Gemini.</summary>

Both are agent programs that run on your computer with your own login, so no key goes in `.env`.
Claude Code needs a Claude Pro, Max, Team, Enterprise or Console plan. Codex needs a ChatGPT account
and [Node.js](https://nodejs.org).

```bash
# Claude Code
irm https://claude.ai/install.ps1 | iex           # Windows PowerShell
curl -fsSL https://claude.ai/install.sh | bash    # macOS / Linux
claude auth login
claude auth status --text                         # are you logged in?

# Codex
npm install -g @openai/codex
codex login
codex login status                                # should print: Logged in using ChatGPT
```

Then set `LLM_PROVIDER=claude` or `LLM_PROVIDER=codex` in `.env`. Open a **new** terminal (so the
program is on your PATH), activate `.venv`, and start `acad serve` from there. The Ask page's model
menu lists every AI; one that cannot run is greyed out with the reason.

</details>

### 3. Check Canvas, and download the search model

```bash
acad check      # "Token OK. Signed in as: <you>", then your courses
acad setup      # a one-time download of the embedding model (about 80 MB)
```

### 4. Start the app and sync your courses

```bash
acad serve      # leave it running, and open http://127.0.0.1:8000
```

On the Courses page, press **Sync with Canvas**. It saves every course and builds the search index,
which takes a few minutes the first time. **While it runs, read
[How it works together](#how-it-works-together).** Then open **Search** and try *"when is the
midterm?"*.

### 5. Ask your first question

Open **Ask**, choose a course, and ask something your course answers. Under the answer, open
**Behind the answer**: what the AI was told, what it was given, and every tool it called. Each task
today shows up there.

**Add your textbook** (or notes, or a practice exam): on the Courses page, open a course and use
**Add your own documents**. It is searchable, with page numbers, a few seconds later.

### If something goes wrong

| If you see | Do this |
|---|---|
| `running scripts is disabled` when activating (Windows) | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again |
| `requires a different Python`, or `No matching distribution found for onnxruntime` | `.venv` was made with Python 3.15 (or older than 3.11). Delete the `.venv` folder, install Python 3.14, and redo step 1 |
| `acad` is not recognized, or not found | Activate `.venv` first (step 1) |
| `CANVAS_BASE_URL is not set` | Run it from the project folder, and check the file is named exactly `.env` (not `.env.txt`) |
| `Canvas rejected the API token (401)` | Copy the token again with no spaces around it, or make a new one |
| The port is already in use | `acad serve --port 8765`, then open http://127.0.0.1:8765 |
| `Gemini needs GEMINI_API_KEY in .env` | Add the key, then stop `acad serve` (Ctrl+C) and start it again |
| Gemini waits, or reports a quota error (429) | The free tier's per-minute limit: it waits and retries by itself; or pick Flash-Lite in the model menu |
| `sent nothing for 120 seconds`, or `503: high demand` | Google is busy for that model: pick another one in the menu, or try again later |
| `claude` or `codex` was not found | Install it, open a new terminal, and start `acad serve` from there |

## Where everything is

```
academic-assistant/
├── .env                  your keys (you make it; never committed)
├── data/                 your courses and the search index (made by Sync; never committed)
├── src/academic_rag/     the code: one folder per step, in order
│   ├── canvas/           1. download your courses from Canvas
│   ├── course_files/     2. turn pages, PDFs and slides into readable text
│   ├── index/            3. chunk, embed and search                 ★ Task 3: search.py
│   ├── tools/            4. what the agent can look up              ★ Task 2: grades.py
│   ├── llm/              5. talk to Gemini, Claude Code or Codex
│   ├── agent/            6. one question: seed, prompt, answer      ★ Task 1: system_prompt.md
│   └── serve/            7. the acad command and the web pages (no need to read)
└── tests/tasks/          one test file per task: it passes when the task is done
```

Every folder has a README: the idea first, then the important code, then something to try. The
three task folders end with their task. `config.py` reads `.env`.

## How it works together

Three journeys explain the whole system. Each step links to the folder that explains it.

### Sync: from Canvas to a search index

1. The Courses page starts a background job on the server ([serve/](src/academic_rag/serve/README.md)).
2. The job asks Canvas for every course and tab, and saves each page, assignment, announcement and
   file as a readable `.md` file under `data/courses/`. Exact due dates, points and grades go in
   `facts.json` ([canvas/](src/academic_rag/canvas/README.md),
   [course_files/](src/academic_rag/course_files/README.md)).
3. Each saved item is cut into chunks of about 1,000 characters. Each chunk becomes 384 numbers, its
   *embedding*, and is stored in a vector database on your computer
   ([index/](src/academic_rag/index/README.md)).

The next Sync only downloads and embeds what changed.

### Search: from a question to passages

```
"when is the midterm?" ──► embed it ──► the 18 nearest chunks ──► rerank (Task 3) ──► the top 6, numbered
```

The question is embedded the same way as the chunks, so *nearest* means *closest in meaning*.
`search()` in [index/search.py](src/academic_rag/index/search.py) does it in a dozen lines.

### Ask: from a question to a cited answer

1. **The seed** ([agent/context.py](src/academic_rag/agent/context.py)): the same search with 20
   results, each widened to its whole page or short document, plus the course's file list and the
   next 14 days of deadlines.
2. **The prompt** ([agent/prompts.py](src/academic_rag/agent/prompts.py)): the rules in
   [system_prompt.md](src/academic_rag/agent/system_prompt.md) (Task 1), the tool list, the seed,
   the conversation and the question.
3. **The answer** ([llm/](src/academic_rag/llm/README.md)): the AI answers, calling tools
   ([tools/](src/academic_rag/tools/README.md)) when the seed is not enough: `search_course` to
   search again, `find_text` for exact names like "Problem 3.5", `read_document` to open a whole
   file, and `grades` once you build it (Task 2).
4. **The page** shows each tool call as it happens, then the answer with links to its sources, and
   **Behind the answer**.

```python
# agent/agent.py, abridged
context = gather(question, ...)                                         # 1. the seed
system, prompt = build_prompt(question, context, tools=tools.specs())   # 2. the prompt
yield from provider.answer(system, prompt, tools, effort)               # 3. tool calls and text
```

Each process has one entry function that reads top to bottom as a list of steps:

| Process | Start reading at |
|---|---|
| Sync | `scrape()` in [canvas/scraper.py](src/academic_rag/canvas/scraper.py), then `embed_courses()` in [index/build.py](src/academic_rag/index/build.py) |
| Search | `search()` in [index/search.py](src/academic_rag/index/search.py) |
| Ask | `ask()` in [agent/agent.py](src/academic_rag/agent/agent.py) |
| A tool call | `Toolbox.call()` in [tools/toolbox.py](src/academic_rag/tools/toolbox.py) |

## The tasks

Pick one. Each guide explains the idea, then the task: the problem, what "done" means, a strategy,
and hints in collapsible sections, so you choose how much help you get.

| # | Task | Suits | Guide | Done when this passes |
|---|---|---|---|---|
| 1 | **Change how the agent answers.** Make explanations end with a "Check yourself" practice question, then try rules of your own. | everyone, no code | [agent/](src/academic_rag/agent/README.md#task-1-change-how-the-agent-answers) | `pytest tests/tasks/test_task1_system_prompt.py` |
| 2 | **Build a tool.** A `grades` tool, so the agent can answer "how am I doing in this class?" from your real scores. About 20 lines. | some Python | [tools/](src/academic_rag/tools/README.md#task-2-build-a-tool) | `pytest tests/tasks/test_task2_grades_tool.py` |
| 3 | **Improve search.** An exact-match boost, so "homework 11.3" finds 11.3, not 11.4. About 15 lines. | comfortable with Python | [index/](src/academic_rag/index/README.md#task-3-improve-search) | `pytest tests/tasks/test_task3_exact_match.py` |

Each takes about 30 minutes. Run the tests in a second terminal with `.venv` activated: they fail
until the task is done, and their messages are hints. Then see it in the app. A prompt change
applies to the next question you ask. For Tasks 2 and 3, stop `acad serve` (Ctrl+C) and start it
again.

**Stuck, or done?** The `solutions` branch has all three. Compare: `git diff main solutions -- src/`.

## How to change the agent

| To change | Look at |
|---|---|
| What it is told: its role, its rules, how it answers | [agent/system_prompt.md](src/academic_rag/agent/system_prompt.md) |
| Which tools it can call | `TOOLS` in [tools/toolbox.py](src/academic_rag/tools/toolbox.py); the tools in [tools/course.py](src/academic_rag/tools/course.py) |
| How much evidence every question starts with | `PASSAGES`, `WHOLE_DOCUMENT_CHARS`, `WHOLE_PAGE_CHARS`, `CONTEXT_CHARS`, `DEADLINE_DAYS` in [agent/context.py](src/academic_rag/agent/context.py) |
| How much of the conversation it remembers | `HISTORY_TURNS`, `HISTORY_CHARS` in [agent/prompts.py](src/academic_rag/agent/prompts.py) |
| How hard it thinks | the reasoning level menu (Medium, High) on the Ask page; the default, `REASONING_LEVEL` in `.env` (blank = medium) |
| How many rounds of tool calls (Gemini) | `MAX_TOOL_ROUNDS` in [llm/gemini.py](src/academic_rag/llm/gemini.py) |
| Which AI and model | the Ask page's model menu (`MODELS` in [llm/catalog.py](src/academic_rag/llm/catalog.py)); the default, `LLM_PROVIDER` and `LLM_MODEL` in `.env` |
| What search returns | [index/search.py](src/academic_rag/index/search.py) (Task 3); chunk size, `CHUNK_CHARS` and `CHUNK_OVERLAP` in `.env` (then `acad embed --rebuild`) |

## Use it from your own code

Everything the web pages do is an HTTP API, and everything the API does is a Python function.

**Over HTTP, from any language, with any of the three AIs.** With `acad serve` running, `POST /ask`
streams an answer back as JSON lines, exactly as the Ask page receives it:

```python
import json

import httpx

body = {"question": "What does Homework 3 cover?", "course": "physics"}
with httpx.stream("POST", "http://127.0.0.1:8000/ask", json=body, timeout=600) as response:
    for line in response.iter_lines():
        event = json.loads(line)
        if event["type"] == "tool":
            print(f"[{event['name']} {event['args']}]")
        elif event["type"] == "text":
            print(event["text"], end="", flush=True)
        elif event["type"] == "error":
            print("Error:", event["message"])
```

Besides `question`, the body can carry `course` (an id or part of its name), `history` (the
conversation so far), and `provider`, `model` and `effort`. Events arrive as `sources` (the first
search), then `tool`, `note` and `text` while it works, then `trace` (what the model was told and
looked up) and `done`, or `error`. Every endpoint, with a form to try it, is at
http://127.0.0.1:8000/docs.

**In Python, in your own process** (Gemini only: Claude Code and Codex reach the tools through the
running server):

```python
from academic_rag import agent
from academic_rag.config import load_settings
from academic_rag.index import get_embedder, open_store
from academic_rag.llm import get_provider
from academic_rag.tools import Toolbox

settings = load_settings()                                   # reads .env
embedder = get_embedder(settings)
store = open_store(settings, embedder.name)
box = Toolbox(settings, store, embedder, course_id="41001")  # a course id from `acad courses`

for event in agent.ask("When is the midterm?", box, get_provider(settings, "gemini")):
    if event["type"] == "text":
        print(event["text"], end="")
print("\nTools used:", [call["name"] for call in box.calls])

# The pieces, with no AI at all
from academic_rag.agent import build_prompt, gather
from academic_rag.index import format_passages, search

print(format_passages(search(store, embedder, "late homework policy", k=5, course_id="41001")))
context = gather("When is the midterm?", settings=settings, store=store, embedder=embedder, course_id="41001")
print(build_prompt("When is the midterm?", context, tools=box.specs())[0])    # the system prompt
print(box.call("find_text", {"text": "Homework 3"}))                          # one tool, run by you
```

**Commands**

| Command | What it does |
|---|---|
| `acad check` | Check the Canvas token; list the courses it can see |
| `acad setup` | Download the embedding model once; report how fast it runs here |
| `acad sync` | Download from Canvas, then index (`acad scrape` and `acad embed` do one half each) |
| `acad courses` | Saved courses, with short summaries and index counts |
| `acad search "when is the midterm" --course physics` | What retrieval finds, in the terminal |
| `acad serve` | The web pages and the API on http://127.0.0.1:8000 (`--port` to change it) |
| `acad eval` | Score the agent on questions whose answers you know ([agent/](src/academic_rag/agent/README.md#going-further)) |

Useful flags: `--course ID` (repeatable), `--no-files`, `--rebuild`, and `-v` for progress.

## Keep it private

- A Canvas token acts as **you**. It stays in `.env`, is only sent to your Canvas host, and never
  appears in logs or responses. Revoke it after the workshop.
- `data/` holds your course content and your grades. `.env` and `data/` are never committed.
- The server only listens on your own computer.
- **The Ask page sends course content to the AI you chose:** the question, the passages found for
  it, the conversation, and every tool result (after Task 2, that can include your grades).
  Syncing, searching and indexing stay on your computer, apart from talking to Canvas.
