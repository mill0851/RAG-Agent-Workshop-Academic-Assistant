# agent/: the answering agent

This is the **generate** half of RAG, behind the **Ask** page. [index/](../index/README.md) finds
passages. This folder gives a language model a strong start (the *seed*), tells it how to use the
course material, and lets it look further with tools until it can answer.

| Step | File | What it does |
|---|---|---|
| | [agent.py](agent.py) | `ask()`: one question, the three steps below, in order |
| 1 | [context.py](context.py) | The seed: a first search, widened to whole pages and short documents, the file list, the deadlines |
| 2 | [system_prompt.md](system_prompt.md) | **Task 1.** What the model is told: its role, how it uses the course material and the tools, how it answers. Plain text |
| 2 | [prompts.py](prompts.py) | `build_prompt()`: that text, plus the tool list, the seed and the conversation |
| 3 | [llm/](../llm/README.md) | A model answers, calling the tools in [tools/](../tools/README.md) when it needs more |

## Architecture diagram

[architecture.mmd](architecture.mmd) draws one turn: the request, the seed, the prompt, the answer
with its tool calls, and the events the Ask page receives. It is a [Mermaid](https://mermaid.js.org)
diagram: plain text that a Mermaid viewer draws as a flowchart. To see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

## The idea

### A turn: seed, prompt, then the model with tools

```
question ─► seed (context.py) ─► prompt (prompts.py) ─► model ─┬─► answer, streamed
              first search, 20 passages                          │
              widened: whole pages, short documents whole        └─► tools when the seed is not enough:
              every file in the course, with its path                search_course · find_text · read_document
              deadlines for the next 14 days
```

**The seed** is classic RAG: embed the question, take the nearest chunks. It is sized to answer most
questions in one step: 20 passages, at most 60,000 characters. A bigger seed costs time on every
question and buries the right passage among weaker ones. When the seed is not enough, the model
looks further by itself.

**Search small, read big.** Search works best on small chunks, because one chunk is about one thing.
Reading works best on whole units, because a chunk can start halfway through a derivation. So
`small_to_big()` in [context.py](context.py) swaps each hit for the unit it came from:

| The hit comes from | The model gets |
|---|---|
| a short document (a homework sheet, an announcement) | the whole document |
| a page or slide of a longer one (a lecture, a textbook) | that whole page or slide, still cited as `p. 212` |
| a long document with no pages, or a huge page | the chunk itself |

### The prompt: enhance the expert, don't fence it in

A frontier model knows a lot about the subjects of your courses. A prompt that says "answer only
from the sources" throws that away: the model hedges or refuses whenever search misses. So
[system_prompt.md](system_prompt.md) leads with expertise, and gives the course material a different
job: making the answer fit *this* course.

```
You are an expert tutor and study assistant for a university student. Use everything you know:
explain, derive, solve, give examples and connect ideas with your own expertise. The student's course
material is given to you to make your help specific to their course, not to limit it.

Using the course material:
- Make answers fit the course: what it covers, how the instructor defines and writes things ...
- Course facts (dates, deadlines, rooms, points, grades, policies, what is assigned) must come from
  the course material or your tools, because you cannot know them otherwise. Never guess them ...
...
How to answer:
- Answer directly and completely, as long as the question needs ...
```

There is **one hard rule**, for the one thing a model cannot know by itself: facts about this
course. Everything else is the model's expertise, made relevant.

### Behind the answer

Every answer ends with a **trace**: exactly what the model was told, what it was given, every tool
call with its result, and the time. On the Ask page it is the **Behind the answer** panel. When an
answer is wrong, it shows why: either the right passage was never found (a search problem), or it
was there and the model misread it (a prompt or model problem).

## Inside one turn, step by step

`ask()` in [agent.py](agent.py) runs one question from start to finish. The server calls it for
every question the Ask page sends, with a fresh Toolbox that knows the chosen course:

```
POST /ask                                serve/agent_routes.py: a Toolbox for this question
└── ask(question, tools, provider)       agent.py
    1. gather()                          context.py: the seed
         search()                          the 20 best-matching chunks (index/search.py)
         small_to_big()                    each one widened to its whole document or page
         due_soon()                        the next 14 days of deadlines (tools/due_soon.py)
         course_file_list()                every item of the course, with its path
       ──► yield "sources"
    2. build_prompt()                    prompts.py: the prompt
         system_prompt()                   system_prompt.md, notes removed, placeholders filled
         the prompt itself                 files, deadlines, sources, conversation, question
    3. provider.answer()                 llm/: the model answers
         the model calls tools             Toolbox.call() runs each one (tools/toolbox.py)
       ──► yield "tool", "note" and "text" while it works
    ──► yield "trace", then "done"
```

**1. The seed.** `gather()` searches the index with the question. A follow-up like *"and which
room?"* means little alone, so it is searched together with the conversation's first question,
which names the topic. The 20 hits go through `small_to_big()`, which sends each document or page
once, at the rank of its best fragment, until about 60,000 characters are in. Then come two exact lists
that search would only approximate: the deadlines of the next 14 days from `facts.json`, and every
item in the course with the path `read_document` opens.

**2. The prompt.** The model gets two texts. The **system prompt** is the same for every question:
the rules in [system_prompt.md](system_prompt.md), read fresh each time, so an edit applies to the
next question. Its notes for people (`<!-- ... -->`) are removed, and three placeholders are
filled in: `{tools}` with one line per tool, `{timezone}`, and `{now}`. The **prompt** is this
question's material: the file list, the deadlines, the numbered sources, the conversation so far,
and the question last.

**3. The answer.** `provider.answer()` sends both texts to the AI you chose, with the tool list. The
model answers, or asks for a tool; the tool runs, and its result goes back to the model, until it
answers. For Gemini our own loop does this ([llm/gemini.py](../llm/gemini.py)), for at most 10
rounds of tool calls. Claude Code and Codex run the loop themselves and reach the same tools over MCP
([llm/](../llm/README.md)). Either way every call goes through `Toolbox.call()`, which keeps the
tools inside the chosen course and records each call ([tools/](../tools/README.md)).

**4. The trace.** When the answer is finished, `ask()` sends everything the model was told and
everything it looked up, which the page shows as **Behind the answer**.

### What the model is given, and where each part comes from

| Part | Comes from | Its limit, and where to change it |
|---|---|---|
| Who it is, its rules, how to answer | [system_prompt.md](system_prompt.md) | Task 1: edit the file |
| The tool list, `{tools}` | The first sentence of each tool's description (`Toolbox.specs()`) | `TOOLS` in [tools/toolbox.py](../tools/toolbox.py) |
| The time zone and the time, `{timezone}` and `{now}` | `TIMEZONE` in `.env`, and the clock | |
| The course's files | Every saved item in the course folder | `FILE_LIST_TITLES`: 200 items |
| The upcoming deadlines | `facts.json`, saved by Sync ([tools/due_soon.py](../tools/due_soon.py)) | `DEADLINE_DAYS`: 14 |
| The sources | `search()` over the index, then `small_to_big()` | `PASSAGES`: 20 hits. `WHOLE_DOCUMENT_CHARS` and `WHOLE_PAGE_CHARS`: 6,000 characters. `CONTEXT_CHARS`: 60,000 in all |
| The conversation | The Ask page sends it with each question | `HISTORY_TURNS`: 20 messages. `HISTORY_CHARS`: 6,000 characters each |
| The question | The Ask page | |

The limits are at the top of [context.py](context.py) and [prompts.py](prompts.py).

### A real prompt

This is a follow-up question in the tests' made-up machine learning course, abridged. The system
prompt is [system_prompt.md](system_prompt.md) without its notes, ending with:

```
Tools: the numbered sources are a first search: a sample of the course, not all of it. When they are not enough, use the tool that fits:
- search_course: Search the course material by meaning and return the most relevant passages, each with its file path.
- find_text: Find every line of the course material that contains this exact text (capitals and spaces are ignored), with its document, page and file path.
- read_document: Read a whole course document (an assignment, a homework PDF, a lecture's slides, the syllabus) with its page labels.
...
The deadline list is exact and already in the student's time zone (America/Chicago). Right now it is Wednesday, October 14, 2026 10:00.
```

The prompt:

```
The course's files (open any of them with read_document and its path):
- announcement: Midterm moved to Oct 28 | courses/Intro to Machine Learning (101)/announcements/2026-10-01-midterm-moved-to-oct-28-7001.md
- file: lecture1.pdf | courses/Intro to Machine Learning (101)/files/Lectures/lecture1.pdf.md
- syllabus: Intro to Machine Learning Syllabus | courses/Intro to Machine Learning (101)/syllabus.md
...

Upcoming deadlines, the next 14 days (exact, from Canvas):
- 2026-10-19 23:59 · Intro to Machine Learning · Homework 2: Logistic Regression (assignment, submitted)

Sources (a first search):
[1] Midterm moved to Oct 28 | Intro to Machine Learning | announcement | score 0.39
file: courses/Intro to Machine Learning (101)/announcements/2026-10-01-midterm-moved-to-oct-28-7001.md
link: https://canvas.test.edu/courses/101/discussion_topics/7001
The midterm exam is now on October 28 in Room 210.

[2] Intro to Machine Learning Syllabus | Intro to Machine Learning | syllabus | score 0.07
file: courses/Intro to Machine Learning (101)/syllabus.md
link: https://canvas.test.edu/courses/101/assignments/syllabus
## Grading

Homework 40%, midterm 25%, final 35%. Late work loses 10% per day.
...

Conversation so far:
Student: When is the midterm exam?

Assistant: The midterm is on October 28 [1].

Student: Which room is it in?
```

The answer is already in source [1], so the model can answer without calling a tool.

### What a turn sends to the page

`ask()` is a generator: it yields events while it works, and the server sends each one to the page
as a line of JSON. The Ask page shows each one as it arrives:

| Event | When | The Ask page |
|---|---|---|
| `sources` | After step 1 | Lists the sources under the answer, and names the AI that answers |
| `tool` | Each time the model calls a tool | Adds a line, such as `find_text Room 210` |
| `note` | When the model says what it is about to do (Codex does) | Adds that line, in italics |
| `text` | Each piece of the answer, as it is written | Shows the answer so far, with math and links for `[1]` |
| `trace` | At the end | Fills **Behind the answer**: the system prompt, the prompt, each tool call and its result |
| `done` | Last | |
| `error` | Instead, when the AI cannot answer (not installed, a bad key) | Shows the message. The server sends it, not `ask()` |

## Try it

- **A fact:** *"When is the midterm?"* Click `[1]` in the answer to open the source.
- **A broad question:** *"What do the homeworks cover?"* Watch the tool calls appear, then open
  **Behind the answer**: which documents did it read?
- **A missing fact:** *"What room is the final in?"* when your course never says. It must not guess.

## Task 1: change how the agent answers

**You'll learn:** how much of an agent's behavior lives in plain text, and how to change it on
purpose.
**Time:** 20-30 minutes. **Suits:** everyone. You edit English, not code, all in
[system_prompt.md](system_prompt.md).

### Part 1: read what the model is told

1. Ask a question on the Ask page, open **Behind the answer**, and read the system prompt.
2. Open [system_prompt.md](system_prompt.md): it is the same text. The note at the top, between
   `<!--` and `-->`, is for you: the model never sees it. `{tools}`, `{timezone}` and `{now}` are
   filled in for every question.
3. Change one word, save, and ask again. **Behind the answer** shows your change: no restart needed.

### Part 2: end every explanation with a practice question

Students remember more when they test themselves right after reading. Change the prompt so that:

| When the answer... | It should |
|---|---|
| explains a concept, a method or a solution | end with one short practice question under the heading **Check yourself**, answerable from the explanation (no answer given) |
| gives a date, a deadline, a policy or another quick fact | not add one: a quiz there is noise |
| | keep every rule that is already there |

**Done when:**
1. `pytest tests/tasks/test_task1_system_prompt.py` passes: the instruction is there, it says when
   to skip the question, and nothing else was lost.
2. On the Ask page, *"Explain [a concept from your course]"* ends with **Check yourself**, and
   *"When is the midterm?"* does not.

**Strategy**

1. **Add, don't rewrite.** One bullet under `How to answer:`. The rules above it are what keep
   answers accurate.
2. **Give the exact heading,** "Check yourself", in quotes, so the model writes it the same way
   every time.
3. **Say when, and when not.** Models follow instructions literally: without "not for dates or
   policies", a question about a deadline ends with a quiz.
4. **Test both kinds of question,** an explanation and a quick fact.

<details>
<summary>Hint: one bullet that works</summary>

```
- When you explain a concept, a method or a solution, end with one short practice question under the heading "Check yourself" that the student can answer from your explanation; do not answer it. Skip it for dates, deadlines, policies and other quick facts.
```

</details>

### Part 3: your own rules

Now make it yours. Change one thing at a time, and ask the same two or three questions before and
after. Some ideas:

- **A Socratic tutor:** for homework problems, give hints and guiding questions instead of the
  full solution.
- **Shorter answers:** at most five sentences, unless the student asks for more.
- **A study planner:** for "what should I study?", turn the deadlines into a day-by-day plan.
- **Another language:** answer in the language the student writes in.

Which changes did what you hoped? Did any of them break a rule that was already there?

### Going further

- **Measure it.** `acad eval` asks questions whose answers you know and scores every answer: was the
  right document found, which facts did it get, how many tools did it call. Copy
  [tests/eval/questions.example.md](../../../tests/eval/questions.example.md) to
  `tests/eval/questions.md`, write about ten questions about your courses, and with `acad serve`
  running, run `acad eval` in a second terminal. Run it before and after a change.
- **Effort.** On a hard problem, compare Medium and High in the Ask page's **Effort** menu. Are the
  answers better, or only slower?
- **Make it a choice.** Some students don't want the quiz. How would you let the Ask page turn it
  off? Which files would change?
