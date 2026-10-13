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
