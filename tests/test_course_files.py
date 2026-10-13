"""The course folders: text extraction, item files, pruning, uploads."""

import os

import pytest

from academic_rag.course_files import (
    CourseStore, Segment, delete_upload, extract_file, html_to_text, prune_tab, read_document, read_meta,
    safe_name, save_upload, slug, write_item,
)

from .conftest import make_docx, make_pdf, make_pptx


def test_html_keeps_structure_and_link_targets():
    html = (
        "<h2>Rubric</h2><p>See the <a href='https://x.edu/rubric'>rubric</a>.</p>"
        "<ul><li>Clarity</li><li>Correctness</li></ul><script>evil()</script>"
    )
    text = html_to_text(html)
    assert "## Rubric" in text
    assert "rubric (https://x.edu/rubric)" in text
    assert "- Clarity" in text and "- Correctness" in text
    assert "evil" not in text


def test_html_empty_inputs():
    assert html_to_text(None) == "" and html_to_text("  ") == ""


def test_pdf_pages_are_labelled():
    segs = extract_file("a.pdf", make_pdf(["first page text", "second page text"]))
    assert [(s.label, s.text) for s in segs] == [("p. 1", "first page text"), ("p. 2", "second page text")]


def test_docx_headings_and_pptx_slides():
    docx_segs = extract_file("g.docx", make_docx("Study Guide", "bias variance"))
    assert "# Study Guide" in docx_segs[0].text and "bias variance" in docx_segs[0].text
    pptx_segs = extract_file("s.pptx", make_pptx("Overfitting", "memorizes noise"))
    assert pptx_segs[0].label == "slide 1" and "memorizes noise" in pptx_segs[0].text


def test_control_characters_from_pdfs_are_removed():
    segs = extract_file("notes.txt", b"Page one\x0cPage two\x00\x01 end\x0b\x7f")
    assert [s.text for s in segs] == ["Page one\nPage two end"]
    assert extract_file("blank.txt", b"\x00\x0c \x01") == []  # nothing left: no text


def test_canvas_file_links_lose_their_access_key():
    html = '<a href="https://c.edu/courses/1/files/9?verifier=SECRET&wrap=1">HW1.pdf</a>'
    assert html_to_text(html) == "HW1.pdf (https://c.edu/courses/1/files/9)"


def test_corrupt_and_unsupported_files_return_empty():
    assert extract_file("bad.pdf", b"not a pdf") == []
    assert extract_file("bad.docx", b"not a docx") == []
    assert extract_file("x.zip", b"PK") == []


def test_safe_name_strips_characters_that_break_paths():
    assert safe_name('CS 101: Intro / "Basics"?') == "CS 101 Intro Basics"
    assert safe_name("  ..hidden.. ") == "hidden"
    assert safe_name("") == "untitled" and safe_name("///") == "untitled"
    assert len(safe_name("x" * 300)) == 80
    assert slug("Week 3 Q&A!") == "week-3-q-a"


def test_course_folder_uses_real_name_and_follows_renames(tmp_path):
    cs = CourseStore(tmp_path)
    d = cs.open_course("93756976", "Organic Chemistry I")
    assert d.name == "Organic Chemistry I (93756976)"
    (d / "course.json").write_text("{}")
    (d / "keep.md").write_text("x")

    renamed = cs.open_course("93756976", "Organic Chemistry I: Honors")
    assert renamed.name == "Organic Chemistry I Honors (93756976)"
    assert (renamed / "keep.md").exists() and not d.exists()
    assert cs.find("93756976") == renamed
    assert cs.find("756976") is None  # an id that is only a suffix does not match
    assert cs.course_dirs() == [renamed]


def test_item_roundtrip_with_page_labels(tmp_path):
    f = tmp_path / "courses" / "Algebra (1)" / "files" / "notes.pdf.md"
    meta = {"doc_id": "course:1:file:9", "course_id": "1", "course_name": "Algebra", "source_type": "file",
            "title": "notes.pdf", "url": "https://c/9", "module": "Week 1", "updated_at": "2026-01-01"}
    segs = [Segment("First page text.", "p. 1"), Segment("Second page text.", "p. 2")]
    assert write_item(f, meta, segs) is True
    assert "<!-- segment: p. 2 -->" in f.read_text()

    doc = read_document(f, tmp_path)
    assert doc.doc_id == "course:1:file:9" and doc.module == "Week 1"
    assert [(s.label, s.text) for s in doc.segments] == [("p. 1", "First page text."), ("p. 2", "Second page text.")]
    assert doc.rel_path == "courses/Algebra (1)/files/notes.pdf.md"
    assert read_document(f, tmp_path, course_name="Renamed").course_name == "Renamed"
    assert read_meta(f)["title"] == "notes.pdf"


def test_unlabelled_text_and_unicode_survive(tmp_path):
    f = tmp_path / "a.md"
    meta = {"doc_id": "d", "course_id": "1", "title": "Résumé: naïve café ✓"}
    write_item(f, meta, [Segment("Plain body — with “quotes” and 日本語.")])
    doc = read_document(f, tmp_path)
    assert doc.title == "Résumé: naïve café ✓" and doc.text == "Plain body — with “quotes” and 日本語."
    assert len(doc.segments) == 1 and doc.segments[0].label == ""


def test_rewriting_identical_content_does_not_touch_the_file(tmp_path):
    f = tmp_path / "a.md"
    meta = {"doc_id": "d", "course_id": "1", "title": "T"}
    write_item(f, meta, [Segment("body")])
    os.utime(f, (1000, 1000))
    assert write_item(f, meta, [Segment("body")]) is False
    assert f.stat().st_mtime == 1000
    assert write_item(f, meta, [Segment("changed")]) is True


def test_files_without_our_header_are_ignored(tmp_path):
    stray = tmp_path / "my own notes.md"
    stray.write_text("# just a note\n\nnot a scraped item")
    assert read_document(stray, tmp_path) is None
    assert read_meta(stray) is None


def test_prune_removes_only_unkept_files_in_that_tab_and_tidies_folders(tmp_path):
    (tmp_path / "announcements").mkdir()
    for n in ("a.md", "b.md"):
        (tmp_path / "announcements" / n).write_text("x")
    (tmp_path / "files" / "Old Folder").mkdir(parents=True)
    (tmp_path / "files" / "Old Folder" / "gone.pdf").write_text("x")
    (tmp_path / "syllabus.md").write_text("x")

    assert prune_tab(tmp_path, "announcements", {"announcements/a.md"}) == 1
    assert (tmp_path / "announcements" / "a.md").exists() and not (tmp_path / "announcements" / "b.md").exists()
    assert prune_tab(tmp_path, "files", set()) == 1
    assert not (tmp_path / "files" / "Old Folder").exists()  # emptied folder removed
    assert (tmp_path / "syllabus.md").exists()  # other tabs untouched
    assert prune_tab(tmp_path, "syllabus", set()) == 1 and not (tmp_path / "syllabus.md").exists()


def test_uploads_are_saved_next_to_their_extracted_text(tmp_path):
    d = tmp_path / "courses" / "Algebra (1)"
    item, meta = save_upload(d, {"id": "1", "name": "Algebra"}, "week 1: notes.txt", b"Matrices multiply row by column.")
    assert (d / "uploads" / "week 1 notes.txt").read_bytes() == b"Matrices multiply row by column."
    assert item == d / "uploads" / "week 1 notes.txt.md"
    assert (meta["doc_id"], meta["source_type"], meta["no_text"]) == ("course:1:upload:week 1 notes.txt", "upload", False)
    doc = read_document(item, tmp_path)
    assert doc.text == "Matrices multiply row by column." and doc.course_name == "Algebra"

    _, meta = save_upload(d, {"id": "1", "name": "Algebra"}, "scan.pdf", make_pdf([""]))
    assert meta["no_text"] is True  # kept, but nothing to index


def test_only_uploads_can_be_deleted_and_both_files_go(tmp_path):
    d = tmp_path / "Algebra (1)"
    (d / "syllabus.md").parent.mkdir(parents=True)
    (d / "syllabus.md").write_text("x")
    item, _ = save_upload(d, {"id": "1", "name": "Algebra"}, "readme.md", b"# Plain markdown, no header")
    with pytest.raises(ValueError, match="Only uploaded files"):
        delete_upload(d, d / "syllabus.md")
    with pytest.raises(ValueError):
        delete_upload(d, d / "uploads" / ".." / "syllabus.md")

    # the original path works too, even when the original is itself a .md file
    assert [p.name for p in delete_upload(d, d / "uploads" / "readme.md")] == ["readme.md", "readme.md.md"]
    assert not item.exists() and (d / "syllabus.md").exists()
    assert delete_upload(d, item) == []  # already gone
