# canvas/: getting data out of a real system

> **You don't need this folder for the tasks.** Read it if you are curious how your courses get
> onto your computer.

Every RAG system starts by collecting the documents it will search. Here the source is Canvas, and
the code does it the way production systems do: politely, safely, and without copying everything
again each time.

| File | What it does |
|---|---|
| [scraper.py](scraper.py) | `scrape()`: picks the courses, then saves each one in seven steps. **Start here** |
| [client.py](client.py) | `CanvasClient`: every request to Canvas, one method per API endpoint |
| [tabs.py](tabs.py) | One handler per tab (pages, assignments, announcements, ...), and `facts.json` |
| [files.py](files.py) | Course files: download, extract the text, keep the original, skip what has not changed |
| [context.py](context.py) | `CourseContext`, what one course's steps share, and `emit()`, which saves each item |
| [naming.py](naming.py) | A readable name and a short summary for each course |

## Architecture diagram

[architecture.mmd](architecture.mmd) draws one sync, from the Canvas API to the saved files: what
Canvas sends, which files are skipped or reused, how HTML and files become labelled text, and where
each result is saved. The orange boxes follow the page and slide labels. It is a
[Mermaid](https://mermaid.js.org) diagram: plain text that a Mermaid viewer draws as a flowchart. To
see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

## A sync, step by step

`scrape()` in [scraper.py](scraper.py) first picks the courses: the ones in `CANVAS_COURSE_IDS` (or
`--course`), or else every course you are enrolled in. Then `scrape_course()` runs the same seven
steps for each course, in this order:

```
scrape()
└── select_courses()                     GET /api/v1/courses
    └── scrape_course(course)            for each course:
        1. name and folder               naming.py: a readable name; data/courses/<name> (<id>)/
        2. load_modules()                GET .../modules, then .../modules/:id/items
        3. _read_tabs()                  GET .../tabs, then one handler per tab:
              home()                       GET .../front_page
              syllabus()                   (came with the course)
              modules()                    (read in step 2)
              pages()                      GET .../pages, then .../pages/:url for each
              assignments()                GET .../assignments?include[]=submission
              quizzes()                    GET .../quizzes
              discussions()                GET .../discussion_topics
              announcements()              GET /api/v1/announcements
              files()                      GET .../files and .../folders, then each download
        4. _save_linked_files()          Files tab hidden: GET .../files/:id for each linked file
        5. facts.json                    the exact values, from step 3 and the course itself
        6. course.json                   name, summary, and the status of every tab
        7. prune                         delete what was deleted in Canvas
```

**1. Name and folder.** Canvas sometimes names a course with a bare number ("93756976").
`resolve_course_name()` then uses the syllabus' own title, or the course code. The folder is
`data/courses/<name> (<id>)/`; when a course is renamed in Canvas, its folder is renamed too.

**2. Modules first.** A module item says which page, assignment, quiz, discussion or file it is. So
the modules are read before any tab, and every later item can look up its module
(`ctx.module_for()`). The File items in modules are also the first list of *linked files*.

**3. Ask, don't assume.** Courses differ: one has Quizzes turned off, another hides Files. The
scraper asks Canvas which tabs this course shows, and runs the handler of each tab it can read, in
the order of `HANDLERS`. Each handler asks for the tab's items, turns each into text, and saves it
with `ctx.emit()`. A tab that answers 403 or 404 is marked *unavailable* and the rest carry on.

**4. Linked files.** Many instructors hide the Files tab and share files through Modules and links
instead. While saving text, `emit()` collects every link like `.../courses/41001/files/123`. If the
Files tab could not be read, each of those files is fetched by its id, which Canvas allows.

**5. facts.json.** Due dates, points, your scores and your course grade, as exact values. Search is
fuzzy; these are not. Every question on the Ask page gets the upcoming deadlines from this file, and
Task 2's `grades` tool reads it ([tools/](../tools/README.md)). A list that could not be read this
run keeps its last saved values.

**6. course.json.** The course's name and where it came from, its term and code, a short summary,
and one status per tab (below).

**7. Prune.** An item deleted in Canvas is deleted here too. Every saved path is remembered in
`ctx.keep`, and any file of that tab not saved this run is removed. Only tabs that were read
completely are pruned. A course whose sync fails partway stops before this step, so a failure
never deletes anything. Your `uploads/` folder is never touched.

## What comes from the Canvas API

Canvas has a JSON API for everything you see in the browser, documented at
https://canvas.instructure.com/doc/api/. These are the only requests this code makes, all `GET`.
Paths under one course are shortened to `...`, which stands for `/api/v1/courses/:id`.

| Request | Method in client.py | What is kept |
|---|---|---|
| `GET /api/v1/users/self` | `get_self()` | Your name, so `acad check` can show the token works |
| `GET /api/v1/courses` | `list_courses()` | Each course's id, name, code, term, home view, syllabus, description and your grade (see the extras below) |
| `GET /api/v1/courses/:id` | `get_course()` | The same, for one course from `CANVAS_COURSE_IDS` |
| `GET .../tabs` | `list_tabs()` | Which tabs the course shows, and their labels |
| `GET .../modules` | `list_modules()` | Each module's name and unlock date |
| `GET .../modules/:id/items` | `list_module_items()` | Each item's type, title and indent, and which page, file or assignment it is |
| `GET .../front_page` | `get_front_page()` | The Home page's title and text |
| `GET .../pages` | `list_pages()` | Each page's address (the list has no text) |
| `GET .../pages/:url` | `get_page()` | One page's title, text and last edit |
| `GET .../assignments?include[]=submission` | `list_assignments()` | Name, description, due, unlock and lock dates, points, how to submit, rubric, and **your submission**: its status, score, grade, and whether it was late, missing or excused |
| `GET .../quizzes` | `list_quizzes()` | Title, description, due date, points, time limit, attempts and number of questions. Not the questions |
| `GET /api/v1/announcements` | `list_announcements()` | Title, text and date. It asks from the year 2000, because by default Canvas returns only the last 14 days |
| `GET .../discussion_topics` | `list_discussions()` | Each topic's title and opening post. Not the replies |
| `GET .../folders` | `list_folders()` | Each folder's path, to rebuild the folder tree |
| `GET .../files` | `list_files()` | Each file's name, folder, size, last change, whether it is locked or hidden, and its download address |
| `GET .../files/:id` | `get_file()` | The same, for one linked file, even when the Files tab is hidden |
| the file's download address | `download()` | The file itself. Canvas redirects it to a separate file server, which never gets the token |

The course requests ask for four extras with `include[]`:

| Extra | What it adds |
|---|---|
| `term` | The term's name, such as "Fall 2026" |
| `syllabus_body` | The Syllabus tab's text, so it needs no request of its own |
| `public_description` | The instructor's short description, used for the summary |
| `total_scores` | Your current and final score and grade, inside the course's `enrollments` |

Lists come back at most 100 items at a time. The address of the next page is in the `Link` header,
and `paginate()` follows it until there is none.

### What is made here, not by Canvas

| What | Made by |
|---|---|
| A readable name when Canvas only has a number | `resolve_course_name()` in [naming.py](naming.py) |
| The short summary on the Courses page | `extractive_summary()` in [naming.py](naming.py): sentences picked from the description or syllabus, plus the module names |
| Text from Canvas's HTML: headings as `#`, lists as `-`, links kept | `html_to_text()` in [course_files/](../course_files/README.md) |
| Text from PDF, Word and PowerPoint files, labelled `p. 3` or `slide 5` | `extract_file()` in [course_files/](../course_files/README.md) |
| Which module each item is in | `load_modules()` in [tabs.py](tabs.py) |
| The list of files the course links to | `emit()` in [context.py](context.py) |
| Folder and file names that work on every computer | `slug()` and `safe_name()` in [course_files/](../course_files/README.md) |
| Each tab's status in `course.json` | `_read_tabs()` in [scraper.py](scraper.py) |

## How the client treats Canvas

**Your token acts as you.** So [client.py](client.py) only sends `GET` requests, and cannot change
anything in Canvas. It refuses a Canvas address that is not `https://`. It attaches the token only
when talking to the Canvas host itself, so the file server that a download redirects to never sees
it:

```python
headers = self._headers if urlparse(url).netloc == urlparse(self.base_url).netloc else {}
```

**Rate limits.** Canvas gives each token a budget of requests that refills over time. `_request()`
reacts to what Canvas answers:

| Canvas answers | The client |
|---|---|
| `200`, with `X-Rate-Limit-Remaining` under 50 | Returns the answer, then pauses half a second |
| `429`, or `403` with "Rate Limit Exceeded" | Waits, then tries again: 1, 2, 4, 8 and 16 seconds, or as long as `Retry-After` says |
| `500` or higher, or no answer at all | The same: waits, then tries again |
| `401` | Raises `CanvasAuthError`: the token is wrong or expired, so the whole sync stops |
| `403` or `404` | Raises `CanvasNotAvailable`: this tab or item is switched off or hidden for you |

After the fifth retry it gives up and raises the error, which stops that course before step 7, so
nothing is pruned. `optional()` in [context.py](context.py) wraps the calls where "not available" just means
"nothing here", such as a hidden page.

## Only what changed

A second sync is quick, because it only saves what changed:

- **Items** are rewritten only when their text changed. `emit()` compares the new text with the
  file on disk, and counts the item as *unchanged* when they match.
- **Files** are downloaded again only when Canvas reports a newer `modified_at`, when the text
  extractor was improved, or when the original is missing. Otherwise the saved text is reused.
- **Deleted items** are pruned, as in step 7.

```bash
acad scrape --course <id>      # the first time: everything is saved and downloaded
acad scrape --course <id>      # again: "0 items saved or updated, ... 0 files downloaded"
```

## What a course folder ends up with

The folder layout and the format of each saved item are described in
[course_files/](../course_files/README.md). The scraper adds two JSON files:

**course.json** describes the course, and records what happened to every tab (abridged):

```json
{
  "id": "41001", "name": "Physics I: Mechanics", "name_source": "canvas_name",
  "course_code": "PHYS 1114", "term": "Fall 2026",
  "summary": "An introduction to Newtonian mechanics ... Topics: Kinematics; Forces; Energy.",
  "tabs": [
    {"id": "pages", "label": "Pages", "status": "scraped", "items": 12},
    {"id": "files", "label": "Files", "status": "unavailable", "items": 0, "note": "not accessible with this token"},
    {"id": "grades", "label": "Grades", "status": "skipped", "items": 0, "note": "grades are saved in facts.json"}
  ],
  "counts": {"page": 12, "assignment": 9, "file": 31}
}
```

| Status | Meaning |
|---|---|
| `scraped` | Read, with at least one item saved |
| `empty` | Read, but it had nothing to save |
| `unavailable` | Canvas answered 403 or 404: the tab is switched off or hidden for you |
| `skipped` | Not read on purpose. The note says why: not course material, an external tool, or `--no-files` |

**facts.json** holds the exact values: your course grade, and every assignment and quiz with its
due date, points and your submission. See the example in [tools/](../tools/README.md#the-problem).

## Try it

```bash
acad check                     # who the token belongs to, and which courses it can see
acad scrape --course <id> -v   # watch it work through one course's tabs
acad scrape --course <id>      # again: "0 items saved or updated"
```

Then open `data/courses/<Course> (<id>)/course.json` and read the `tabs` list: which tabs were read,
which were empty, and which were skipped and why. Open one of the `.md` files next to the Canvas
page it came from: what did the conversion to text keep, and what did it lose?
