"""The HTTP API the web front end will call."""

import threading

from fastapi.testclient import TestClient

from academic_rag.index import HashEmbedder
from academic_rag.serve import api as api_module
from academic_rag.serve.api import create_app

from .conftest import TOKEN, make_pdf, wait


def test_health_before_anything_is_scraped(client, settings):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["canvas_configured"] is True
    assert (body["courses"], body["indexed_chunks"], body["embedder"]) == (0, 0, "hash-768")
    assert client.get("/courses").json() == []
    assert client.get("/courses/101").status_code == 404


def test_scrape_job_reports_progress_and_result(client):
    job = client.post("/scrape").json()
    assert job["kind"] == "scrape" and job["status"] in {"queued", "running", "done"}
    view = wait(client, job)
    assert view["status"] == "done" and view["error"] is None
    assert view["total"] == 2 and view["done"] == 2
    assert "2 course(s)" in view["result"]["summary"]
    assert {c["name"] for c in view["result"]["courses"]} == {"Intro to Machine Learning", "Organic Chemistry I"}
    assert client.get("/jobs").json()[0]["id"] == job["id"]
    assert client.get("/jobs/nope").status_code == 404


def test_courses_expose_what_a_front_end_needs(loaded):
    courses = {c["id"]: c for c in loaded.get("/courses").json()}
    assert set(courses) == {"101", "102"}
    ml = courses["101"]
    assert ml["name"] == "Intro to Machine Learning" and ml["course_code"] == "CS 4731" and ml["term"] == "Fall 2026"
    assert "Late work loses 10% per day" in ml["summary"] and ml["indexed_chunks"] > 0
    assert ml["counts"]["file"] == 5 and any(t["id"] == "modules" for t in ml["tabs"])
    assert courses["102"]["name"] == "Organic Chemistry I" and courses["102"]["name_source"] == "syllabus_title"
    assert loaded.get("/courses/101").json()["id"] == "101"
    assert loaded.get("/health").json()["indexed_chunks"] == sum(c["indexed_chunks"] for c in courses.values())


def test_facts_and_document_listing(loaded):
    facts = loaded.get("/courses/101/facts").json()
    assert facts["grade"]["current_grade"] == "A-" and facts["assignments"][0]["name"].startswith("Homework 2")
    docs = loaded.get("/courses/101/documents", params={"type": "assignment"}).json()
    assert sorted(d["title"] for d in docs) == ["Homework 2: Logistic Regression", "Project Proposal"]
    assert docs[0]["path"].startswith("courses/Intro to Machine Learning (101)/assignments/")
    scanned = [d for d in loaded.get("/courses/101/documents", params={"type": "file"}).json() if d["title"] == "scanned.pdf"]
    assert scanned and scanned[0]["has_text"] is False


def test_search_returns_citation_ready_passages(loaded):
    body = loaded.post("/search", json={"query": "When is the midterm exam?", "top_k": 2}).json()
    top = body["passages"][0]
    assert top["n"] == 1 and top["title"] == "Midterm moved to Oct 28" and top["type"] == "announcement"
    assert top["course"] == "Intro to Machine Learning" and top["url"].endswith("/discussion_topics/7001")
    assert set(top) == {"n", "title", "course", "course_id", "type", "location", "module", "url", "path", "score", "text"}

    by_name = loaded.post("/search", json={"query": "office hours", "course": "organic"}).json()["passages"]
    assert by_name and all(p["course_id"] == "102" for p in by_name)
    typed = loaded.post("/search", json={"query": "grading", "course": "101", "source_types": ["syllabus"]}).json()
    assert typed["passages"] and all(p["type"] == "syllabus" for p in typed["passages"])


def test_search_errors_are_clear(loaded):
    assert loaded.post("/search", json={"query": "x", "course": "o"}).status_code == 404  # ambiguous
    assert "Ambiguous course" in loaded.post("/search", json={"query": "x", "course": "o"}).json()["detail"]
    assert loaded.post("/search", json={"query": ""}).status_code == 422
    assert loaded.post("/search", json={"query": "x", "top_k": 99}).status_code == 422


def test_sync_does_scrape_and_embed_in_one_job(client):
    view = wait(client, client.post("/sync").json())
    assert view["status"] == "done" and "chunks embedded" in view["result"]["summary"]
    assert client.post("/search", json={"query": "midterm"}).json()["passages"]


def test_embed_can_be_limited_and_rebuilt(loaded):
    view = wait(loaded, loaded.post("/embed", json={"course_ids": ["102"], "rebuild": True}).json())
    assert view["result"]["new"] == 1


def test_only_one_job_runs_at_a_time(loaded, monkeypatch):
    client = loaded
    gate = threading.Event()
    class Stats:
        def summary(self):
            return "stub"

        courses = []

    monkeypatch.setattr(api_module.canvas, "scrape", lambda *a, **k: (gate.wait(10), Stats())[1])
    first = client.post("/scrape").json()
    busy = client.post("/embed")
    assert busy.status_code == 409 and "already running" in busy.json()["detail"]
    assert client.get("/health").json()["busy_job"] == first["id"]
    assert upload(client).status_code == 409  # uploads and deletes write the index too
    in_course = "courses/Intro to Machine Learning (101)/uploads/x.pdf"
    assert client.delete("/courses/101/files", params={"path": in_course}).status_code == 409
    gate.set()
    assert wait(client, first)["status"] == "done"
    assert client.post("/embed").status_code == 202  # free again


def test_scrape_needs_a_token_and_bad_tokens_fail_the_job_cleanly(canvas, settings):
    settings.canvas_api_token = ""
    app = TestClient(create_app(settings, embedder=HashEmbedder()))
    r = app.post("/scrape")
    assert r.status_code == 400 and "CANVAS_API_TOKEN" in r.json()["detail"]

    settings.canvas_api_token = "wrong-token-xyz"
    view = wait(app, app.post("/scrape").json())
    assert view["status"] == "error" and "rejected the API token" in view["error"]
    assert "wrong-token-xyz" not in view["error"]


def test_errors_never_leak_url_query_strings():
    err = api_module._clean_error(RuntimeError("failed for https://c.edu/files/1/download?verifier=SECRET&x=1 today"))
    assert "SECRET" not in err and "?..." in err


def test_saved_files_can_be_served_but_nothing_else(loaded):
    docs = loaded.get("/courses/101/documents", params={"type": "syllabus"}).json()
    r = loaded.get("/courses/101/files", params={"path": docs[0]["path"]})
    assert r.status_code == 200 and "Late work loses 10% per day" in r.text
    pdf = [d for d in loaded.get("/courses/101/documents", params={"type": "file"}).json() if d["title"] == "lecture1.pdf"][0]
    assert loaded.get("/courses/101/files", params={"path": pdf["path"][:-3]}).content.startswith(b"%PDF")

    assert loaded.get("/courses/101/files", params={"path": "courses/Intro to Machine Learning (101)/course.json"}).status_code == 400
    assert loaded.get("/courses/101/files", params={"path": "../../../etc/passwd"}).status_code == 400
    # another course's file through this course's URL
    other = loaded.get("/courses/102/documents").json()[0]["path"]
    assert loaded.get("/courses/101/files", params={"path": other}).status_code == 400
    assert loaded.get("/courses/999/files", params={"path": other}).status_code == 404


def test_cors_allows_the_local_front_end_only(client):
    ok = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "https://evil.example"}).headers


def test_the_canvas_token_is_never_in_any_response(loaded):
    paths = ["/health", "/courses", "/courses/101", "/courses/101/facts", "/courses/101/documents", "/jobs", "/openapi.json"]
    bodies = [loaded.get(p).text for p in paths]
    bodies.append(loaded.post("/search", json={"query": "midterm"}).text)
    assert all(TOKEN not in b for b in bodies)
    assert "paths" in loaded.get("/openapi.json").json()  # schema for generating a typed client


# ------------------------------------------------------------- course files
PCA = "Eigenvectors of the covariance matrix give the principal components."


def upload(client, course="101", name="my notes.pdf", data=None):
    data = make_pdf([PCA]) if data is None else data
    return client.post(f"/courses/{course}/files", files={"file": (name, data)})


def find(client, query, k=20):
    return client.post("/search", json={"query": query, "top_k": k}).json()["passages"]


def test_upload_is_searchable_downloadable_and_deletable(loaded):
    r = upload(loaded)
    assert r.status_code == 201
    item = r.json()
    assert item["path"] == "courses/Intro to Machine Learning (101)/uploads/my notes.pdf.md"
    assert item["has_text"] is True and item["chunks"] >= 1

    top = find(loaded, "principal components covariance eigenvectors", 1)[0]
    assert (top["type"], top["title"], top["location"], top["path"]) == ("upload", "my notes.pdf", "p. 1", item["path"])
    assert [d["path"] for d in loaded.get("/courses/101/documents", params={"type": "upload"}).json()] == [item["path"]]

    original = item["path"][: -len(".md")]
    dl = loaded.get("/courses/101/files", params={"path": original})
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    assert dl.headers["content-disposition"].startswith("attachment")

    gone = loaded.delete("/courses/101/files", params={"path": original})
    assert gone.status_code == 200 and gone.json()["removed"] == [original, item["path"]]
    assert all(p["type"] != "upload" for p in find(loaded, "principal components covariance eigenvectors"))
    assert loaded.get("/courses/101/files", params={"path": original}).status_code == 404
    assert loaded.delete("/courses/101/files", params={"path": original}).status_code == 404


def test_uploading_the_same_name_replaces_the_file(loaded):
    upload(loaded, name="notes.txt", data=b"Version one mentions kangaroos.")
    upload(loaded, name="notes.txt", data=b"Version two mentions platypuses.")
    texts = [p["text"] for p in find(loaded, "kangaroos platypuses") if p["type"] == "upload"]
    assert texts == ["Version two mentions platypuses."]


def test_bad_uploads_are_refused(loaded, settings):
    assert upload(loaded, name="setup.exe", data=b"MZ").status_code == 400
    assert upload(loaded, course="999").status_code == 404
    settings.max_upload_bytes = 10
    big = upload(loaded, name="big.txt", data=b"x" * 11)
    assert big.status_code == 413 and "MAX_UPLOAD_MB" in big.json()["detail"]
    assert loaded.get("/courses/101/documents", params={"type": "upload"}).json() == []


def test_only_uploads_can_be_deleted(loaded):
    syllabus = loaded.get("/courses/101/documents", params={"type": "syllabus"}).json()[0]["path"]
    r = loaded.delete("/courses/101/files", params={"path": syllabus})
    assert r.status_code == 400 and "Only uploaded files" in r.json()["detail"]
    assert loaded.get("/courses/101/files", params={"path": syllabus}).status_code == 200
    assert loaded.delete("/courses/101/files", params={"path": "../../../etc/passwd"}).status_code == 400


def test_uploads_survive_a_new_sync(loaded):
    path = upload(loaded).json()["path"]
    assert wait(loaded, loaded.post("/sync").json())["status"] == "done"
    assert loaded.get("/courses/101/files", params={"path": path}).status_code == 200
    assert find(loaded, "principal components covariance eigenvectors", 1)[0]["type"] == "upload"


def test_a_course_filter_applies_to_one_job_only(client, settings):
    view = wait(client, client.post("/scrape", json={"course_ids": ["102"]}).json())
    assert [c["id"] for c in view["result"]["courses"]] == ["102"]
    view = wait(client, client.post("/scrape").json())
    assert {c["id"] for c in view["result"]["courses"]} == {"101", "102"} and settings.course_ids == []


# --------------------------------------------------------------- web pages
def test_the_web_pages_are_served_next_to_the_api(client):
    home = client.get("/")
    assert home.status_code == 200 and "Sync with Canvas" in home.text
    assert 'api("/search"' in client.get("/search.html").text
    assert client.get("/app.js").status_code == 200 and client.get("/style.css").status_code == 200
    assert client.get("/health").json()["status"] == "ok"  # API routes still win over the pages
