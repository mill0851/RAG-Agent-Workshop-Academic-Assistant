"""Saved course folders -> chunks -> local index -> retrieval."""

import os

import pytest

from academic_rag.canvas import CanvasClient, scrape
from academic_rag.course_files import Document, Segment, list_courses
from academic_rag.index import (
    HashEmbedder, IndexMismatch, VectorStore, chunk_document, embed_courses, format_passages,
    load_manifest, resolve_course, search, split_text,
)

from .conftest import BASE, TOKEN, make_pdf


# ----------------------------------------------------------------- chunking
def test_chunks_respect_size_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(12))
    chunks = split_text(text, 400, 80)
    assert len(chunks) > 3
    assert all(len(c) <= 400 + 20 for c, _ in chunks)
    # overlap: the start of each chunk repeats the tail of the previous one
    for (a, _), (b, _) in zip(chunks, chunks[1:]):
        assert b[:30] in a


def test_heading_is_never_stranded_alone_and_is_tracked():
    text = "# Intro\n\n" + ("alpha beta gamma. " * 30) + "\n\n## Loss\n\nCross entropy is a loss."
    chunks = split_text(text, 200, 30)
    assert all(c.strip() not in {"# Intro", "## Loss"} for c, _ in chunks)
    assert chunks[0][1] == "Intro"
    assert chunks[-1][1] in {"Intro", "Loss"}


def test_oversized_unbroken_text_is_hard_split():
    chunks = split_text("x" * 1000, 300, 50)
    assert all(len(c) <= 300 for c, _ in chunks)
    assert sum(len(c) for c, _ in chunks) >= 1000


def test_chunk_metadata_and_embedding_header():
    doc = Document(
        doc_id="course:1:file:9", course_id="1", course_name="ML", source_type="file",
        title="lecture.pdf", url="https://c/f/9", module="Week 2",
        segments=[Segment("alpha " * 20, "p. 1"), Segment("beta " * 20, "p. 2")],
    )
    chunks = chunk_document(doc, 500, 50)
    assert [c.metadata["location"] for c in chunks] == ["p. 1", "p. 2"]
    assert chunks[0].chunk_id == "course:1:file:9#0" and chunks[1].chunk_id.endswith("#1")
    assert chunks[0].embed_text.startswith("ML > Week 2 > lecture.pdf (p. 1)")
    assert chunks[0].text == ("alpha " * 20).strip()
    assert all(isinstance(v, (str, int, float, bool)) for c in chunks for v in c.metadata.values())


def test_chunks_the_model_cannot_read_whole_are_cut_until_they_fit():
    sentences = [f"Step {i}: d sigma / d t = {i} x 10^-3 in units of N/m^2." for i in range(40)]
    doc = Document(doc_id="course:1:file:9", course_id="1", course_name="ML", source_type="file",
                   title="notes.pdf", url="", segments=[Segment(" ".join(sentences), "p. 4")])
    fits = lambda text: len(text.split()) <= 60  # a model that reads at most 60 words  # noqa: E731

    capped = chunk_document(doc, 1000, 100, fits)
    assert len(capped) > len(chunk_document(doc, 1000, 100))  # the target size alone is too big
    assert all(fits(c.embed_text) for c in capped)
    assert all(c.metadata["location"] == "p. 4" for c in capped)
    assert [c.chunk_id for c in capped] == [f"course:1:file:9#{i}" for i in range(len(capped))]
    joined = " ".join(c.text for c in capped)
    assert all(s in joined for s in sentences)  # cut on sentence breaks: nothing lost
    assert chunk_document(doc, 1000, 100, lambda text: True) == chunk_document(doc, 1000, 100)
    assert HashEmbedder().fits("word " * 10_000)  # the test embedder has no limit


# ------------------------------------------------------ embedding and search
ML = "Intro to Machine Learning (101)"


class Rig:
    def __init__(self, settings, embedder=None):
        self.settings = settings
        self.embedder = embedder or HashEmbedder()
        self.store = VectorStore(settings.index_dir, self.embedder.name)
        self.root = settings.courses_dir

    def embed(self, **kw):
        return embed_courses(self.settings, self.store, self.embedder, **kw)

    def search(self, q, k=3, **kw):
        return search(self.store, self.embedder, q, k, **kw)


def indexed_courses(rig):
    return {m["course_id"] for m in load_manifest(rig.settings.manifest_path).values()}


@pytest.fixture
def rig(canvas, settings):
    scrape(settings, client=CanvasClient(BASE, TOKEN))
    return Rig(settings)


def test_first_embed_covers_every_source_type(rig):
    stats = rig.embed()
    assert stats.by_type == {
        "home": 1, "syllabus": 2, "module": 2, "page": 1, "assignment": 2, "quiz": 1,
        "discussion": 1, "announcement": 1,
        "file": 4,  # pdf, docx, pptx, txt. The scanned PDF has no text and is not indexed.
    }
    assert stats.new == 15 and stats.updated == 0 and stats.unchanged == 0 and rig.store.count() == stats.chunks_written
    titles = {p.title for p in rig.search("anything at all", 100)}
    assert "scanned.pdf" not in titles and "secret.pdf" not in titles and "data.zip" not in titles


def test_retrieval_returns_citable_passages(rig):
    rig.embed()
    top = rig.search("When is the midterm exam?", 1)[0]
    assert (top.n, top.type, top.title) == (1, "announcement", "Midterm moved to Oct 28")
    assert top.url.endswith("/discussion_topics/7001") and top.course == "Intro to Machine Learning"
    assert top.path.endswith("announcements/2026-10-01-midterm-moved-to-oct-28-7001.md")

    top = rig.search("backpropagation chain rule gradients", 1)[0]
    assert (top.title, top.location, top.module) == ("lecture1.pdf", "p. 1", "Week 2: Generalization")
    assert top.url == f"{BASE}/courses/101/files/9001" and top.path.endswith("files/Lectures/lecture1.pdf.md")

    top = rig.search("homework 2 logistic regression due date", 1)[0]
    assert top.title.startswith("Homework 2") and "Due: 2026-10-20" in top.text

    # an assignment with no description is still findable by name
    assert rig.search("project proposal due", 1)[0].title == "Project Proposal"
    assert rig.search("quiz time limit linear models", 1)[0].type == "quiz"
    assert [p.n for p in rig.search("learning rate", 3)] == [1, 2, 3]


def test_course_and_type_filters(rig):
    rig.embed()
    hits = rig.search("office hours", 5, course_id="102")
    assert hits and all(h.course_id == "102" for h in hits)
    hits = rig.search("grading late work", 5, course_id="101", source_types=["syllabus"])
    assert hits and all(h.type == "syllabus" for h in hits)
    assert rig.search("midterm", 5, source_types=["not-a-type"])  # unknown types are ignored, not fatal
    assert indexed_courses(rig) == {"101", "102"}
    assert rig.search("   ") == []


def test_course_name_resolution_for_filters(rig):
    rig.embed()
    courses = list_courses(rig.settings)
    assert resolve_course(courses, "102") == ("102", None)
    assert resolve_course(courses, "organic") == ("102", None)
    assert resolve_course(courses, "o")[0] is None and "Ambiguous" in resolve_course(courses, "o")[1]
    assert "No course matches" in resolve_course(courses, "physics")[1]


def test_format_passages_for_display(rig):
    rig.embed()
    text = format_passages(rig.search("backpropagation", 2), max_chars=80)
    assert text.startswith("[1] lecture1.pdf (p. 1) | Intro to Machine Learning > Week 2: Generalization | file")
    assert "link: https://canvas.test.edu/courses/101/files/9001" in text and "[2]" in text


def test_second_run_is_a_no_op(rig):
    rig.embed()
    count = rig.store.count()
    stats = rig.embed()
    assert (stats.new, stats.updated, stats.removed, stats.chunks_written) == (0, 0, 0, 0)
    assert stats.unchanged == 15 and rig.store.count() == count


def test_editing_a_saved_file_re_embeds_only_that_file(rig):
    rig.embed()
    syllabus = rig.root / ML / "syllabus.md"
    syllabus.write_text(syllabus.read_text().replace("Late work loses 10% per day.", "Late work is never accepted."))
    stats = rig.embed()
    assert (stats.new, stats.updated, stats.removed) == (0, 1, 0)
    top = rig.search("late work never accepted", 1, course_id="101", source_types=["syllabus"])[0]
    assert "never accepted" in top.text
    assert not any("loses 10%" in h.text for h in rig.search("late work", 20, course_id="101"))


def test_scraping_a_canvas_edit_flows_through_to_the_index(canvas, rig):
    rig.embed()
    canvas.assignments["101"][5001]["description"] = "<p>Now implement softmax regression instead.</p>"
    scrape(rig.settings, client=CanvasClient(BASE, TOKEN))
    stats = rig.embed()
    assert (stats.new, stats.updated, stats.removed) == (0, 1, 0)
    assert rig.search("softmax regression", 1)[0].title.startswith("Homework 2")


def test_deleted_items_and_folders_are_removed_from_the_index(rig):
    rig.embed()
    (rig.root / ML / "pages" / "gradient-descent-11.md").unlink()
    assert rig.embed().removed == 1
    assert all(h.type != "page" for h in rig.search("negative gradient learning rate", 30))

    import shutil

    shutil.rmtree(rig.root / "Organic Chemistry I (102)")
    stats = rig.embed()
    assert stats.removed >= 1 and indexed_courses(rig) == {"101"}


def test_a_course_filter_never_prunes_other_courses(rig):
    rig.embed()
    before = rig.store.count()
    stats = rig.embed(course_ids=["102"])
    assert stats.removed == 0 and rig.store.count() == before


def test_rebuild_wipes_and_reindexes_everything_or_just_named_courses(rig):
    rig.embed()
    n = rig.store.count()
    stats = rig.embed(rebuild=True)
    assert stats.unchanged == 0 and stats.new == 15 and rig.store.count() == n
    stats = rig.embed(rebuild=True, course_ids=["102"])
    assert stats.new == 1 and rig.store.count() == n  # only the Chemistry syllabus was redone


def test_emptied_document_is_dropped_from_the_index(rig):
    rig.embed()
    path = rig.root / ML / "quizzes" / "quiz-1-linear-models-6001.md"
    head = path.read_text().split("\n---\n", 1)[0]
    path.write_text(head + "\n---\n\n# Quiz 1: Linear Models\n")
    assert rig.embed().removed == 1
    assert all(h.type != "quiz" for h in rig.search("quiz", 30))


def test_stray_notes_without_a_header_are_ignored(rig):
    (rig.root / ML / "my own notes.md").write_text("# remember\n\nbuy milk, unrelated to the course")
    assert rig.embed().new == 15
    assert not any("buy milk" in h.text for h in rig.search("milk", 30))


def test_files_copied_into_the_uploads_folder_are_indexed_on_the_next_embed(rig):
    rig.embed()
    uploads = rig.root / ML / "uploads"
    uploads.mkdir()
    book = uploads / "Deep Learning textbook.pdf"
    book.write_bytes(make_pdf(["Chapter 9. Convolutional networks share weights across positions."]))
    assert rig.embed().new == 1  # extracted and embedded, no upload button needed
    hit = rig.search("convolutional networks share weights", 1)[0]
    assert (hit.title, hit.type, hit.location) == ("Deep Learning textbook.pdf", "upload", "p. 1")
    assert rig.embed().unchanged == 16 and rig.embed().new == 0  # nothing new: not extracted again

    os.utime(book, (book.stat().st_mtime + 10,) * 2)  # replaced with a newer version
    book.write_bytes(make_pdf(["Chapter 9. Pooling layers summarize nearby activations."]))
    os.utime(book, (book.stat().st_mtime + 10,) * 2)
    assert rig.embed().updated == 1
    assert "Pooling" in rig.search("pooling layers", 1)[0].text

    book.unlink()  # deleted from the folder: gone from the index too
    assert rig.embed().removed == 1 and not (uploads / "Deep Learning textbook.pdf.md").exists()


def test_course_rename_re_embeds_with_the_new_name(canvas, rig):
    rig.embed()
    canvas.courses["101"]["name"] = "Machine Learning Honors"
    scrape(rig.settings, client=CanvasClient(BASE, TOKEN))
    stats = rig.embed()
    assert stats.updated == 14 and stats.unchanged == 1  # everything in course 101; Chemistry untouched
    assert {p.course for p in rig.search("learning", 30, course_id="101")} == {"Machine Learning Honors"}


def test_progress_is_reported_and_ends_at_the_total(rig):
    calls = []
    rig.embed(progress=lambda msg, done, total: calls.append((done, total)))
    assert calls[-1][0] == calls[-1][1] > 0
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)


def test_chunks_from_many_documents_are_embedded_in_shared_batches(rig):
    class Counting(HashEmbedder):
        def __init__(self):
            super().__init__()
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(len(texts))
            return super().embed_documents(texts)

    counting = Counting()
    rig = Rig(rig.settings, counting)
    stats = rig.embed()
    assert sum(counting.calls) == stats.chunks_written
    assert len(counting.calls) < stats.new  # fewer model calls than documents


def test_an_interrupted_run_keeps_its_progress(rig):
    class Flaky(HashEmbedder):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("laptop went to sleep")
            return super().embed_documents(texts)

    flaky_rig = Rig(rig.settings, Flaky())
    with pytest.raises(RuntimeError):
        flaky_rig.embed(batch_chunks=4)
    done = len(load_manifest(rig.settings.manifest_path))
    assert 0 < done < 15

    stats = Rig(rig.settings).embed(batch_chunks=4)  # resumes: only the rest is embedded
    assert stats.unchanged == done and stats.new == 15 - done


def test_switching_embedding_backend_needs_a_full_rebuild(rig):
    rig.embed()
    other = Rig(rig.settings, HashEmbedder(dim=384))  # a different backend on the same index
    with pytest.raises(IndexMismatch, match="acad embed --rebuild"):
        other.search("midterm")
    with pytest.raises(IndexMismatch):
        other.embed()
    with pytest.raises(IndexMismatch):
        other.embed(rebuild=True, course_ids=["102"])  # a partial rebuild would mix the two

    stats = other.embed(rebuild=True)
    assert stats.new == 15 and other.search("midterm", 1)[0].title == "Midterm moved to Oct 28"
    with pytest.raises(IndexMismatch):  # the index now belongs to the new backend
        VectorStore(rig.settings.index_dir, rig.embedder.name).check()
