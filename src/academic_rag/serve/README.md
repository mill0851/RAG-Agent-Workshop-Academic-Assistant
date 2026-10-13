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

## Try it

- Open http://127.0.0.1:8000/docs, expand `POST /search`, press **Try it out**, and run a search
  without the web page.
- In the web page, open your browser's developer tools (F12) → **Network**, then press Search: the
  request the page sends, and the JSON that comes back.
