# serve/: the acad command, the API and the web pages

> **You don't need this folder for the tasks,** and you never need to change the web pages: when
> you finish a task, the pages show the result.

One backend, several front doors: the web pages and the `acad` command call the same Python
functions through this folder.

| File | What it does |
|---|---|
| [api.py](api.py) | `create_app()`: the HTTP API (FastAPI) for courses, files, search and sync, and the web pages |
| [agent_routes.py](agent_routes.py) | The agent's routes: `/ask`, the MCP endpoint, `/models`, `/due` |
| [state.py](state.py) | What the routes share (the index, the AIs, the open questions), and the request shapes |
| [jobs.py](jobs.py) | Background jobs, one writer at a time |
| [cli.py](cli.py) | The `acad` command |
| [eval.py](eval.py) | `acad eval`: questions with known answers, asked through the running server |
| [static/](static) | The three web pages: plain HTML, a little JavaScript and a stylesheet, no build step |

## Architecture diagram

[architecture.mmd](architecture.mmd) draws the front doors (the web pages, the `acad` command, your
own programs, and Claude Code and Codex), the routes they reach, and the folders behind them. It is
a [Mermaid](https://mermaid.js.org) diagram: plain text that a Mermaid viewer draws as a flowchart.
To see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

## The idea

**The API is the front door.** Every action in the web pages is one HTTP request, and those same
requests are what another program would make. FastAPI writes interactive documentation for every
endpoint at http://127.0.0.1:8000/docs.

**A route is a thin layer over a function.** Each endpoint checks its input, calls a function from
another folder, and returns JSON. That is what lets the `acad` command and the agent's tools reuse
the same code:

```python
@app.post("/search")
def search(req: SearchRequest) -> dict:    # abridged
    course_id, problem = index.resolve_course(course_files.list_courses(settings), req.course)
    passages = index.search(state.store(), state.embedder(), req.query, req.top_k, course_id=course_id)
    return {"query": req.query, "passages": [p.to_dict() for p in passages]}
```

**Long work runs in the background.** A first Sync can take minutes, longer than a browser should
wait for one request. So `POST /sync` starts a background job and returns its id straight away, and
the page asks `GET /jobs/{id}` for progress about once a second.

**One writer at a time.** The index and the course folders must never be written by two operations
at once. So the server runs one job at a time: a second Sync or upload while one runs gets *409
Conflict*. For the same reason, don't run `acad embed` in a terminal while `acad serve` is running.

**Local and careful.** The server listens only on your own computer, never returns the Canvas token,
and only serves files from inside a course's folder.

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /courses` · `/courses/{id}` · `/courses/{id}/facts` | saved courses; their exact due dates, points and grades |
| `GET /courses/{id}/documents` | the course's saved items |
| `POST` · `GET` · `DELETE /courses/{id}/files` | add a document; download a saved item or its original; delete an upload |
| `POST /search` | `{query, course?, top_k?}` → numbered passages with citations |
| `POST /ask` | `{question, history?, course?, provider?, model?, effort?}` → a streamed, cited answer ([agent/](../agent/README.md)) |
| `POST /mcp/{session}` | the tools of the question in progress, for Claude Code and Codex ([llm/](../llm/README.md)) |
| `GET /models` · `GET /due?days=7` | the AIs the Ask page can offer; what is due soon |
| `POST /scrape` · `/embed` · `/sync` | start a background job → `GET /jobs/{id}` for progress |

## Inside one request, step by step

`acad serve` runs `create_app()` in [api.py](api.py), then serves it on `http://127.0.0.1:8000`:

```
create_app()
1. State                  the embedder, the index and the AIs, each opened the first time it is needed
2. the routes             courses, files, search and jobs (api.py); /ask, /mcp, /models, /due (agent_routes.py)
3. the web pages          static/, mounted last at /, so every API route wins
```

Four journeys show how a request becomes work:

**A sync.** The Courses page's **Sync with Canvas** sends `POST /sync`. `JobManager.submit()` in
[jobs.py](jobs.py) takes the one writer slot, starts a background thread, and returns the job's id
straight away. The thread runs the scraper, then the embed. Meanwhile the page asks `GET /jobs/{id}`
every 0.7 seconds and shows the message and the count.

**An upload.** `POST /courses/{id}/files` checks the file's type and size, takes the writer slot for
the length of the request, saves the file and its text ([course_files/](../course_files/README.md)),
and indexes the course before it answers. That is why an upload is searchable the moment it returns.

**A search.** `POST /search` turns the course name into an id, runs `index.search()`, and returns
the numbered passages as JSON. No AI is involved.

**A question.** `POST /ask` builds a Toolbox for the question and gives it an MCP address with an
unguessable token, for Claude Code and Codex. It then streams the events of `agent.ask()` back, one
JSON line each. When the answer ends, the address closes.

### What each page calls

| Page | Requests |
|---|---|
| Courses ([index.html](static/index.html)) | `GET /health`, `/courses`, `/due?days=7`, `/courses/{id}/documents` · `POST /sync`, then `GET /jobs/{id}` · `POST`, `GET` and `DELETE /courses/{id}/files` |
| Search ([search.html](static/search.html)) | `GET /courses` · `POST /search` |
| Ask ([ask.html](static/ask.html)) | `GET /courses`, `/models` · `POST /ask` |

### What the status codes mean

| Code | When |
|---|---|
| `400` | The request cannot be done: Canvas is not set up in `.env`, the AI cannot be used, a file type is not supported, or a path is outside the course |
| `404` | No such course, file, job, or question in progress |
| `409` | Another job is running, or the index was built with another embedder |
| `413` | The upload is larger than `MAX_UPLOAD_MB` |
| `422` | The request's JSON does not have the right shape (FastAPI checks it against [state.py](state.py)) |

## Try it

- Open http://127.0.0.1:8000/docs, expand `POST /search`, press **Try it out**, and run a search
  without the web page.
- In the web page, open your browser's developer tools (F12) → **Network**, then press Search: the
  request the page sends, and the JSON that comes back.
