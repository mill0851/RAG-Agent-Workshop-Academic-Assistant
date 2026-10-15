"""Canvas: the read-only client, course names and summaries, and scraping into course folders."""

import json

import httpx
import pytest
import respx

from academic_rag.canvas import CanvasAuthError, CanvasClient, CanvasNotAvailable
from academic_rag.canvas import scrape as run_scrape
from academic_rag.canvas.naming import (
    extractive_summary, looks_like_id, resolve_course_name, summary_inputs_hash, syllabus_title,
)
from academic_rag.course_files import read_document, read_json, read_meta

from .conftest import BASE, TOKEN, make_pdf


# ------------------------------------------------------------------ client
def client(token=TOKEN):
    return CanvasClient(BASE, token)


def test_follows_link_header_pagination_and_drops_nameless_courses(canvas):
    courses = client().list_courses(["active"])
    assert [c["id"] for c in courses] == [101, 102]  # 103 is date-restricted (no name)
    pages = [r for r in canvas.requests if r.url.path == "/api/v1/courses"]
    assert len(pages) == 2
    assert pages[0].url.params["per_page"] == "100"


def test_each_enrollment_state_is_requested_separately_and_deduplicated(canvas):
    courses = client().list_courses(["active", "completed"])
    assert sorted(c["id"] for c in courses) == [101, 102]
    states = {r.url.params["enrollment_state"] for r in canvas.requests if r.url.path == "/api/v1/courses"}
    assert states == {"active", "completed"}


def test_course_listing_asks_for_syllabus_term_and_scores(canvas):
    client().list_courses(["active"])
    first = [r for r in canvas.requests if r.url.path == "/api/v1/courses"][0]
    assert {"syllabus_body", "term", "total_scores", "public_description"} <= set(first.url.params.get_list("include[]"))


def test_bad_token_raises_auth_error(canvas):
    with pytest.raises(CanvasAuthError):
        client("wrong").get_self()


def test_disabled_tab_is_not_available_not_a_crash(canvas):
    with pytest.raises(CanvasNotAvailable):
        client().list_files("102")


def test_rate_limit_403_is_retried(canvas):
    canvas.rate_limit_next = 2
    assert client().get_self()["name"] == "Test Student"
    assert len(canvas.requests) == 3


def test_announcements_ask_for_full_history(canvas):
    client().list_announcements("101")
    req = [r for r in canvas.requests if r.url.path == "/api/v1/announcements"][0]
    assert req.url.params["start_date"] == "2000-01-01"
    assert req.url.params["context_codes[]"] == "course_101"


def test_token_is_not_sent_to_the_cdn_after_redirect(canvas):
    data = client().download(f"{BASE}/files/9001/download?verifier=abc", 10_000_000)
    assert data.startswith(b"%PDF")
    cdn = [r for r in canvas.requests if r.url.host == "cdn.test.edu"]
    assert cdn and all("authorization" not in r.headers for r in cdn)


def test_token_is_not_sent_to_any_other_host_even_without_a_redirect(canvas):
    # httpx already strips auth on cross-origin redirects; this covers a file URL
    # that points straight at another host, which our own host check must handle.
    data = client().download("https://cdn.test.edu/files/9001/lecture1.pdf", 10_000_000)
    assert data.startswith(b"%PDF")
    assert "authorization" not in canvas.requests[-1].headers


def test_oversize_download_returns_none(canvas):
    assert client().download(f"{BASE}/files/9001/download", 10) is None


def test_refuses_plain_http_for_remote_hosts():
    with pytest.raises(ValueError):
        CanvasClient("http://canvas.school.edu", "t")


def test_localhost_http_allowed_for_dev():
    CanvasClient("http://localhost:3000", "t")


def test_persistent_server_error_eventually_raises():
    with respx.mock() as router:
        router.route().mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            CanvasClient(BASE, TOKEN, max_retries=2).get_self()


def test_token_never_appears_in_repr_or_errors(canvas):
    from academic_rag.config import Settings

    s = Settings(canvas_api_token=TOKEN)
    assert TOKEN not in repr(s)
    with pytest.raises(CanvasAuthError) as e:
        client("wrong-token-xyz").get_self()
    assert "wrong-token-xyz" not in str(e.value)


# --------------------------------------------------- course name and summary
@pytest.mark.parametrize("value", ["93756976", "  4455 ", "Course 12345", "12-345", "ab", "", None, "Untitled"])
def test_ids_and_placeholders_are_not_real_names(value):
    assert looks_like_id(value)


@pytest.mark.parametrize("value", ["Organic Chemistry I", "CS 101", "ENGR 1411-001 Fall 2026", "Biology 2"])
def test_real_names_are_kept(value):
    assert not looks_like_id(value)


def test_name_resolution_order():
    # a real Canvas name always wins
    assert resolve_course_name("Intro to ML", "CS 4731", "# Other Syllabus", "1") == ("Intro to ML", "canvas_name")
    # numeric Canvas name -> the syllabus' own title
    syl = "# Organic Chemistry I Syllabus\n\nWelcome to the course."
    assert resolve_course_name("93756976", "CHEM 2053", syl, "9") == ("Organic Chemistry I", "syllabus_title")
    # no usable syllabus title -> course code
    assert resolve_course_name("93756976", "CHEM 2053", "Welcome to the course!", "9") == ("CHEM 2053", "course_code")
    # nothing usable -> id
    assert resolve_course_name("4455", None, "", "4455") == ("Course 4455", "id")
    assert resolve_course_name("4455", "4455", "", "4455") == ("Course 4455", "id")


def test_syllabus_title_variants():
    assert syllabus_title("Course Syllabus - Biology 2\n\ntext") == "Biology 2"
    assert syllabus_title("## PHYS 1114: Mechanics - Syllabus") == "PHYS 1114: Mechanics"
    assert syllabus_title("Syllabus\n\nbody text") is None  # nothing left once the word is removed
    assert syllabus_title("Welcome everyone, glad you are here") is None


SYLLABUS = (
    "# Syllabus\n\n"
    "This course introduces machine learning: supervised and unsupervised methods, model evaluation, "
    "and practical implementation in Python. Students complete three projects.\n\n"
    "Contact: prof@school.edu\n\n## Grading\n\n- Homework 40%\n\n"
    "Late work is penalized ten percent per day unless an extension was approved in advance."
)


def test_summary_uses_syllabus_prose_and_module_topics():
    summary, source = extractive_summary("", SYLLABUS, ["Week 1: Optimization", "Week 2: Generalization"])
    assert summary.startswith("This course introduces machine learning")
    assert "prof@school.edu" not in summary  # contact blocks are skipped
    assert summary.endswith("Topics: Optimization; Generalization.")
    assert source == "syllabus+modules"
    assert len(summary) < 600


def test_summary_prefers_instructor_description_and_handles_empty_inputs():
    desc = "A hands-on introduction to modern machine learning for engineers."
    assert extractive_summary(desc, SYLLABUS, []) == (desc, "description")
    assert extractive_summary("", "", []) == ("", "none")
    assert extractive_summary("", "", ["Week 1: Intro"]) == ("Topics: Intro.", "modules")


def test_summary_is_cut_on_a_sentence_boundary():
    long = " ".join(f"Sentence number {i} says something useful about the course." for i in range(40))
    summary, _ = extractive_summary(long, "", [], max_chars=200)
    assert len(summary) <= 200 and summary.endswith(".")


def test_inputs_hash_changes_with_any_input():
    base = summary_inputs_hash("d", "s", ["m"])
    assert base == summary_inputs_hash("d", "s", ["m"])
    assert len({base, summary_inputs_hash("d2", "s", ["m"]), summary_inputs_hash("d", "s2", ["m"]),
                summary_inputs_hash("d", "s", ["m", "n"])}) == 4


# ----------------------------------------------------------------- scraping
ML = "Intro to Machine Learning (101)"
CHEM = "Organic Chemistry I (102)"


def scrape(settings, **kw):
    return run_scrape(settings, client=CanvasClient(BASE, TOKEN), **kw)


def downloads(canvas):
    return sum(r.url.host == "cdn.test.edu" for r in canvas.requests)


@pytest.fixture
def root(canvas, settings):
    scrape(settings)
    return settings.courses_dir


def test_folders_use_real_course_names_even_when_canvas_name_is_a_number(root):
    assert sorted(p.name for p in root.iterdir()) == [ML, CHEM]
    chem = read_json(root / CHEM / "course.json")
    assert chem["name"] == "Organic Chemistry I" and chem["name_source"] == "syllabus_title"
    assert chem["canvas_name"] == "93756976" and chem["course_code"] == "CHEM 2053" and chem["id"] == "102"
    ml = read_json(root / ML / "course.json")
    assert ml["name_source"] == "canvas_name" and ml["term"] == "Fall 2026"


def test_course_json_has_a_short_summary_for_the_front_end(root):
    ml = read_json(root / ML / "course.json")
    assert "Late work loses 10% per day" in ml["summary"]
    assert ml["summary"].endswith("Topics: Optimization; Generalization.")
    assert ml["summary_source"] == "syllabus+modules" and len(ml["summary"]) < 600
    assert read_json(root / CHEM / "course.json")["summary"].startswith("This course covers the structure")


def test_every_tab_is_accounted_for(root):
    tabs = {t["id"]: t for t in read_json(root / ML / "course.json")["tabs"]}
    for scraped in ("home", "syllabus", "modules", "pages", "assignments", "quizzes",
                    "discussions", "announcements", "files"):
        assert tabs[scraped]["status"] == "scraped" and tabs[scraped]["items"] > 0, scraped
    assert tabs["grades"]["status"] == "skipped" and "facts.json" in tabs["grades"]["note"]
    assert tabs["people"]["status"] == "skipped"
    assert tabs["context_external_tool_9"]["status"] == "skipped"

    chem = {t["id"]: t["status"] for t in read_json(root / CHEM / "course.json")["tabs"]}
    assert chem == {"home": "empty", "syllabus": "scraped", "announcements": "empty", "files": "unavailable"}


def test_tab_content_lands_in_readable_markdown(root):
    ml = root / ML
    assert "Office hours are Mondays at 3pm" in (ml / "home.md").read_text()
    assert "Late work loses 10% per day" in (ml / "syllabus.md").read_text()
    assert sorted(p.name for p in (ml / "pages").iterdir()) == ["gradient-descent-11.md"]  # front page is home.md
    assert [p.name for p in (ml / "announcements").iterdir()] == ["2026-10-01-midterm-moved-to-oct-28-7001.md"]
    assert [p.name for p in (ml / "discussions").iterdir()] == ["week-3-q-a-8001.md"]  # announcement duplicate skipped
    assert "October 28 in Room 210" in next((ml / "announcements").iterdir()).read_text()

    week1 = (ml / "modules" / "01-week-1-optimization.md").read_text()
    assert "- [Page] Gradient Descent" in week1 and "## Readings" in week1
    assert "- [Link] Deep Learning Book (https://www.deeplearningbook.org)" in week1
    assert "- [Quiz] Quiz 1: Linear Models" in (ml / "modules" / "02-week-2-generalization.md").read_text()

    quiz = (ml / "quizzes" / "quiz-1-linear-models-6001.md").read_text()
    assert "Time limit: 30 minutes" in quiz and "normal equations" in quiz


def test_assignments_include_rubric_and_exist_even_without_a_description(root):
    hw = (root / ML / "assignments" / "homework-2-logistic-regression-5001.md").read_text()
    assert "Due: 2026-10-20T04:59:00Z" in hw and "Points: 100" in hw and "Submission: online_upload" in hw
    assert "Correctness (60 pts): Gradients are right" in hw and "  - 0 pts: No credit" in hw
    proposal = (root / ML / "assignments" / "project-proposal-5002.md").read_text()
    assert "Due: 2026-11-05T05:59:00Z" in proposal and "Points: 20" in proposal


def test_items_know_their_module(root):
    page = read_document(root / ML / "pages" / "gradient-descent-11.md", root.parent)
    assert page.module == "Week 1: Optimization"
    pdf = read_document(root / ML / "files" / "Lectures" / "lecture1.pdf.md", root.parent)
    assert pdf.module == "Week 2: Generalization"
    assert read_document(root / ML / "quizzes" / "quiz-1-linear-models-6001.md", root.parent).module == "Week 2: Generalization"


def test_files_keep_canvas_folder_structure_originals_and_page_labels(root):
    files = root / ML / "files"
    assert (files / "Lectures" / "lecture1.pdf").read_bytes().startswith(b"%PDF")
    assert (files / "Lectures" / "overfitting.pptx").exists() and (files / "study_guide.docx").exists()
    assert not (files / "Lectures" / "reading_list.txt").exists()  # text types: only the extraction is kept
    assert "convolutional networks" in (files / "Lectures" / "reading_list.txt.md").read_text()
    assert not list(files.rglob("data.zip*")) and not list(files.rglob("secret.pdf*"))  # unsupported / locked

    doc = read_document(files / "Lectures" / "lecture1.pdf.md", root.parent)
    assert [s.label for s in doc.segments] == ["p. 1", "p. 2"]
    slides = read_document(files / "Lectures" / "overfitting.pptx.md", root.parent)
    assert slides.segments[0].label == "slide 1"
    assert doc.url == f"{BASE}/courses/101/files/9001"  # a Canvas link, not the tokenised download URL


def test_scanned_pdf_is_recorded_as_having_no_text(root):
    meta = read_meta(root / ML / "files" / "scanned.pdf.md")
    assert meta["no_text"] is True and (root / ML / "files" / "scanned.pdf").exists()
    assert read_document(root / ML / "files" / "scanned.pdf.md", root.parent).text == ""


def test_facts_json_holds_exact_dates_and_grades(root):
    facts = read_json(root / ML / "facts.json")
    assert facts["grade"] == {"current_score": 91.5, "current_grade": "A-", "final_score": 88.0, "final_grade": "B+"}
    assert [a["name"] for a in facts["assignments"]] == ["Homework 2: Logistic Regression", "Project Proposal"]
    hw = facts["assignments"][0]
    assert hw["due_at"] == "2026-10-20T04:59:00Z" and hw["points_possible"] == 100 and hw["module"] == "Week 1: Optimization"
    assert hw["submission"]["score"] == 88.0 and hw["submission"]["workflow_state"] == "graded"
    assert facts["assignments"][1]["submission"]["workflow_state"] == "unsubmitted"
    assert facts["quizzes"][0]["title"] == "Quiz 1: Linear Models" and facts["quizzes"][0]["time_limit"] == 30
    assert read_json(root / CHEM / "facts.json")["grade"] is None


def test_no_secrets_are_written_to_disk(root):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in {".md", ".json"}:
            text = p.read_text()
            assert TOKEN not in text and "verifier" not in text, p


def test_second_run_changes_nothing_and_downloads_nothing(canvas, settings, root):
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    first = downloads(canvas)
    stats = scrape(settings)
    assert (stats.written, stats.removed, stats.downloads) == (0, 0, 0) and stats.unchanged > 0
    assert downloads(canvas) == first
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file() and p.name != "facts.json"
            and p.name != "course.json"} == {p: t for p, t in before.items() if p.name not in {"facts.json", "course.json"}}


def test_edits_in_canvas_update_only_that_item(canvas, settings, root):
    canvas.assignments["101"][5001]["description"] = "<p>Now implement softmax regression instead.</p>"
    stats = scrape(settings)
    assert stats.written == 1 and stats.downloads == 0
    assert "softmax regression" in (root / ML / "assignments" / "homework-2-logistic-regression-5001.md").read_text()


def test_updated_file_is_downloaded_again(canvas, settings, root):
    canvas.files["101"][0]["modified_at"] = "2026-10-01T00:00:00Z"
    canvas.blobs["/files/9001/lecture1.pdf"] = make_pdf(["Lecture 1 revised. Momentum accelerates gradient descent."])
    stats = scrape(settings)
    assert stats.downloads == 1
    assert "Momentum" in (root / ML / "files" / "Lectures" / "lecture1.pdf.md").read_text()


def test_items_deleted_in_canvas_are_removed_unless_no_prune(canvas, settings, root):
    del canvas.pages["101"]["gradient-descent"]
    canvas.announcements["101"].clear()
    canvas.files["101"] = [f for f in canvas.files["101"] if f["id"] != 9001]
    assert scrape(settings, prune=False).removed == 0
    assert (root / ML / "pages" / "gradient-descent-11.md").exists()

    assert scrape(settings).removed == 4  # the page, the announcement, and the pdf plus its extracted text
    assert not (root / ML / "pages").exists() or not list((root / ML / "pages").iterdir())
    assert not (root / ML / "announcements").exists()
    assert not (root / ML / "files" / "Lectures" / "lecture1.pdf").exists()
    assert not (root / ML / "files" / "Lectures" / "lecture1.pdf.md").exists()
    assert (root / ML / "files" / "Lectures" / "overfitting.pptx.md").exists()  # neighbours untouched


def test_no_files_run_neither_downloads_nor_prunes_files(canvas, settings, root):
    first = downloads(canvas)
    stats = scrape(settings, include_files=False)
    assert stats.removed == 0 and downloads(canvas) == first
    tabs = {t["id"]: t for t in read_json(root / ML / "course.json")["tabs"]}
    assert tabs["files"]["status"] == "skipped"
    assert (root / ML / "files" / "Lectures" / "lecture1.pdf").exists()


def test_failed_run_deletes_nothing(canvas, settings, root):
    before = sorted(p for p in root.rglob("*") if p.is_file())
    del canvas.pages["101"]["gradient-descent"]
    canvas.rate_limit_next = 10**6  # Canvas throttles forever -> the run aborts
    with pytest.raises(httpx.HTTPStatusError):
        scrape(settings)
    assert sorted(p for p in root.rglob("*") if p.is_file()) == before


def test_renaming_a_course_in_canvas_renames_the_folder(canvas, settings, root):
    canvas.courses["101"]["name"] = "Machine Learning: Honors"
    scrape(settings)
    assert sorted(p.name for p in root.iterdir()) == ["Machine Learning Honors (101)", CHEM]
    assert (root / "Machine Learning Honors (101)" / "files" / "Lectures" / "lecture1.pdf").exists()
    meta = read_document(root / "Machine Learning Honors (101)" / "home.md", root.parent)
    assert meta.course_name == "Machine Learning: Honors"  # item headers follow the rename


def test_an_llm_written_summary_is_kept_until_the_course_text_changes(canvas, settings, root):
    path = root / ML / "course.json"
    data = json.loads(path.read_text())
    data.update(summary="A friendly LLM summary.", summary_source="llm")
    path.write_text(json.dumps(data))
    scrape(settings)
    assert read_json(path)["summary"] == "A friendly LLM summary."

    canvas.courses["101"]["syllabus_body"] = "<p>Completely rewritten syllabus text that is long enough to be prose here.</p>"
    scrape(settings)
    after = read_json(path)
    assert after["summary_source"] != "llm" and "rewritten syllabus" in after["summary"]


def test_course_id_filter_and_unknown_ids(canvas, settings, tmp_path):
    settings.course_ids = ["102", "999"]
    stats = scrape(settings)
    assert [c.id for c in stats.courses] == ["102"]
    assert [p.name for p in settings.courses_dir.iterdir()] == [CHEM]


def test_tab_list_unavailable_falls_back_to_trying_everything(canvas, settings):
    # a Canvas that answers 404 for /tabs: every handler is attempted instead
    original = canvas.handle

    def handle(request):
        if request.url.path.endswith("/tabs"):
            return httpx.Response(404, json={"errors": []})
        return original(request)

    import respx

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handle)
        scrape(settings)
    assert (settings.courses_dir / ML / "syllabus.md").exists()
    assert (settings.courses_dir / ML / "assignments").is_dir()


def test_scraping_never_touches_uploads(canvas, settings, root):
    upload = root / ML / "uploads" / "my notes.txt.md"
    upload.parent.mkdir()
    upload.write_text("x")
    assert scrape(settings).removed == 0 and upload.exists()


def test_a_course_filter_does_not_change_the_settings(canvas, settings):
    assert [c.id for c in scrape(settings, course_ids=["102"]).courses] == ["102"]
    assert settings.course_ids == [] and len(scrape(settings).courses) == 2


def test_a_hidden_files_tab_still_saves_the_files_the_course_links_to(canvas, settings):
    # Many instructors hide the Files tab and share files through Modules and links instead.
    canvas.tabs["101"] = [t for t in canvas.tabs["101"] if t["id"] != "files"]
    canvas.files_enabled["101"] = False
    canvas.assignments["101"][5002]["description"] = (
        '<p>Use the <a href="https://canvas.test.edu/courses/101/files/9002?verifier=SECRET123&wrap=1">'
        "study guide</a>.</p>")
    scrape(settings)
    files = settings.courses_dir / ML / "files"
    assert (files / "Lectures" / "lecture1.pdf.md").exists()      # a File item in Modules
    assert (files / "Lectures" / "overfitting.pptx.md").exists()   # another one
    assert (files / "study_guide.docx.md").exists()                # linked from an assignment
    assert not list(files.rglob("reading_list*"))                  # linked from nowhere: not reachable
    tabs = {t["id"]: t for t in read_json(settings.courses_dir / ML / "course.json")["tabs"]}
    assert tabs["files"]["status"] == "scraped" and "links to" in tabs["files"]["note"]


def test_file_links_are_saved_without_their_access_key(canvas, settings):
    canvas.assignments["101"][5002]["description"] = (
        '<a href="https://canvas.test.edu/courses/101/files/9002?verifier=SECRET123&wrap=1">study guide</a>')
    scrape(settings)
    text = (settings.courses_dir / ML / "assignments" / "project-proposal-5002.md").read_text(encoding="utf-8")
    assert "study guide (https://canvas.test.edu/courses/101/files/9002)" in text
    assert "SECRET123" not in text and "verifier" not in text
