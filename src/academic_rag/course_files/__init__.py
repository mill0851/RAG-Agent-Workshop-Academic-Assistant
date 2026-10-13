"""The course folders on disk: the hand-off between the Canvas scraper and the index.
extract.py turns files into text; folders.py is the folder layout; items.py reads and
writes the saved items; uploads.py adds your own files; courses.py answers questions
about the saved courses. See README.md in this folder."""

from .courses import course_dir, get_course, get_facts, list_courses, list_documents
from .extract import EXTRACTOR_VERSION, SUPPORTED_EXTENSIONS, Segment, extract_file, html_to_text
from .folders import CourseStore, prune_tab, read_json, safe_name, slug, write_bytes, write_json
from .items import Document, count_markdown, iter_documents, read_document, read_meta, write_item
from .uploads import UPLOADS, delete_upload, save_upload, sync_uploads_folder
