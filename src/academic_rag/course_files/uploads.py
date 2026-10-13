"""Your own files in a course: saved in <course folder>/uploads/ like any other item,
so they are searched exactly like Canvas files. Canvas never touches this folder.

Two ways in: the Courses page (save_upload), or copying files into the uploads/ folder
yourself; the next embed (Sync, or `acad embed`) picks those up (sync_uploads_folder).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .extract import SUPPORTED_EXTENSIONS, extract_file
from .folders import safe_name, write_bytes
from .items import read_meta, write_item

UPLOADS = "uploads"  # not a Canvas tab, so the scraper never prunes it


def save_upload(course_dir: Path, course: dict, filename: str, data: bytes) -> tuple[Path, dict]:
    """Save an uploaded file and its extracted text. Uploading the same name again
    replaces it. Returns (path of the .md item, its header)."""
    name = safe_name(filename, 120)
    original = course_dir / UPLOADS / name
    write_bytes(original, data)
    segments = extract_file(name, data)  # the same extraction as Canvas files
    meta = {
        "doc_id": f"course:{course['id']}:upload:{name}", "course_id": str(course["id"]),
        "course_name": course.get("name", ""), "source_type": "upload", "title": name, "url": "",
        "module": "", "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "no_text": not segments, "size": len(data),
    }
    item = original.with_name(name + ".md")
    write_item(item, meta, segments)
    return item, meta


def sync_uploads_folder(course_dir: Path, course: dict) -> dict:
    """Files copied into <course>/uploads/ by hand: extract the new or changed ones, and drop
    the text of any whose file was deleted. Returns {"added": [...], "removed": [...]}."""
    folder = course_dir / UPLOADS
    if not folder.is_dir():
        return {"added": [], "removed": []}
    items = {p.name for p in folder.glob("*.md") if (read_meta(p) or {}).get("doc_id")}  # our .md items
    added, removed = [], []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name not in items):
        if PurePosixPath(path.name.lower()).suffix not in SUPPORTED_EXTENSIONS or path.name.endswith(".tmp"):
            continue
        item = path.with_name(path.name + ".md")
        if item.name in items and item.stat().st_mtime >= path.stat().st_mtime:
            continue  # extracted already, and the file has not changed since
        if safe_name(path.name, 120) != path.name:  # a name the rest of the system cannot use: rename it
            path = path.rename(path.with_name(safe_name(path.name, 120)))
        save_upload(course_dir, course, path.name, path.read_bytes())
        added.append(path.name)
    for name in items:
        if not (folder / name[: -len(".md")]).exists():  # the file itself was deleted
            (folder / name).unlink()
            removed.append(name[: -len(".md")])
    return {"added": added, "removed": removed}


def delete_upload(course_dir: Path, target: Path) -> list[Path]:
    """Delete an uploaded file and its extracted text (either path works).
    Raises ValueError for anything outside the course's uploads folder."""
    target = target.resolve()
    if not target.is_relative_to((course_dir / UPLOADS).resolve()):
        raise ValueError("Only uploaded files can be deleted; everything else mirrors Canvas.")
    item = target if read_meta(target) else target.with_name(target.name + ".md")
    original = item.with_name(item.name[: -len(".md")])
    removed = [p for p in (original, item) if p.is_file()]
    for p in removed:
        p.unlink()
    return removed
