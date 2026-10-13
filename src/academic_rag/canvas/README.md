# canvas/: getting data out of a real system

> **You don't need this folder for the tasks.** Read it if you are curious how your courses get
> onto your computer.

Every RAG system starts by collecting the documents it will search. Here the source is Canvas, and
the code does it the way production systems do: politely, safely, and without copying everything
again each time.

| File | What it does |
|---|---|
| [scraper.py](scraper.py) | `scrape()`: walk each course, run one handler per tab, write `course.json` and `facts.json`. Start here |
| [client.py](client.py) | `CanvasClient`: every HTTP request to Canvas goes through it |
| [tabs.py](tabs.py) | One function per tab (pages, assignments, announcements, ...), and the exact facts |
| [files.py](files.py) | Download course files, only when they changed |
| [context.py](context.py) | What one course's handlers share, and the counts a sync reports |
| [naming.py](naming.py) | A real name and a short summary for each course |

## The idea

**A REST API and a personal token.** Canvas has a JSON API for everything you see in the browser:
`GET /api/v1/courses/41001/assignments` returns that course's assignments. Your token acts as *you*,
so this code only sends `GET` requests (it cannot change anything in Canvas). It only attaches the
token when talking to the Canvas host itself, so the server that hosts file downloads never sees it:

```python
headers = self._headers if urlparse(url).netloc == urlparse(self.base_url).netloc else {}
```

**Pages and rate limits.** Canvas returns at most 100 items per request, with the address of the
next page in a `Link` header; the client follows those links. When the token's request budget runs
out, Canvas answers "Rate Limit Exceeded", and the client waits and tries again, longer each time.

**Ask, don't assume.** Courses differ: one has Quizzes turned off, another hides Files. The scraper
asks Canvas which tabs a course has and reads only those. When Files is hidden, it still fetches
every file the course's pages and modules link to.

**From Canvas JSON to a saved item.** Each tab handler turns Canvas JSON into readable text and
saves it. An announcement like
`{"title": "Midterm exam: date and room", "message": "<p>The midterm exam is on ...</p>"}` becomes
`announcements/2026-10-05-midterm-exam-date-and-room-41001020.md`, in the format described in
[course_files/](../course_files/README.md).

**Only what changed.** A file is rewritten only when its text changed, and downloaded again only
when Canvas says it was modified. Items deleted in Canvas are deleted locally, but only after every
tab of that course was read without errors, so a sync that fails halfway never deletes anything.

**Exact facts on the side.** Due dates, points, your scores and your course grade are saved in
`facts.json` as exact values. Search is fuzzy; these are not. Every question gets the upcoming
deadlines from them, and Task 2's `grades` tool reads them ([tools/](../tools/README.md)).

## Try it

```bash
acad check                     # who the token belongs to, and which courses it can see
acad scrape --course <id> -v   # watch it work through one course's tabs
acad scrape --course <id>      # again: "0 items saved or updated"
```

Then open `data/courses/<Course> (<id>)/course.json` and read the `tabs` list: which tabs were read,
which were empty, and which were skipped and why.
