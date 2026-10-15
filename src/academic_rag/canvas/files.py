"""Course files: download each PDF, Word file, slide deck or text file, save its text for
search, and keep the original next to it. A file is downloaded again only when Canvas
says it changed since the last sync.

    files()          the Files tab: GET .../files lists every file, with a download URL
    linked_files()   the Files tab is hidden: GET .../files/:id for each file the course
                     links to from its modules, pages and assignments

For lecture1.pdf in the Canvas folder "Lectures", the course folder gets:

    files/Lectures/lecture1.pdf      the original (only for PDF, Word and PowerPoint)
    files/Lectures/lecture1.pdf.md   its text, one segment per page: what gets indexed
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from ..course_files import (
    EXTRACTOR_VERSION, SUPPORTED_EXTENSIONS, Segment, extract_file, read_document, read_meta, safe_name,
    write_bytes,
)
from .context import CourseContext, optional

log = logging.getLogger(__name__)

BINARY_EXTENSIONS = {".pdf", ".docx", ".pptx"}  # originals kept next to their text (others: the text only)


def files(ctx: CourseContext) -> None:
    """The Files tab: every file the course shows there."""
    _save_files(ctx, ctx.client.list_files(ctx.cid))


def linked_files(ctx: CourseContext) -> None:
    """The files the course links to from its modules, pages and assignments, fetched one by
    one. Many courses hide the Files tab but still share files this way."""
    found = [optional(lambda i=i: ctx.client.get_file(ctx.cid, i), f"file {i}", None)
             for i in sorted(ctx.linked_files, key=int)]
    _save_files(ctx, [f for f in found if f])


def _save_files(ctx: CourseContext, files: list[dict]) -> None:
    """Save each file Canvas listed: skip, reuse or download it, then save its text."""
    # The Canvas folders, by id: each file names its folder by id ("folder_id")
    folders = {f["id"]: f for f in optional(lambda: ctx.client.list_folders(ctx.cid), "folders", [])}
    taken: set[str] = set()  # local paths already used in this run, in lowercase

    for f in files:
        name = safe_name(f.get("display_name") or f.get("filename") or str(f["id"]), 120)
        ext = PurePosixPath(name.lower()).suffix
        if _skip(ctx, f, name, ext):
            continue

        path = _local_path(f, name, ext, folders, taken)  # files/Lectures/lecture1.pdf
        text_path = path + ".md"  # its text: what gets indexed
        original = path if ext in BINARY_EXTENSIONS else None  # the original is kept for these
        updated = f.get("modified_at") or f.get("updated_at") or ""

        previous = read_meta(ctx.cdir / text_path)  # what the last sync saved, if anything
        if _unchanged(ctx, previous, f["id"], updated, original):
            # Unchanged in Canvas: reuse the text on disk instead of downloading again.
            old = read_document(ctx.cdir / text_path, ctx.data_root)
            segments, no_text = (old.segments if old else []), bool(previous.get("no_text"))
        else:
            downloaded = _download(ctx, f, name, original)
            if downloaded is None:
                if (ctx.cdir / text_path).exists():  # could not download it again: keep the last copy
                    ctx.keep["files"].update({text_path, *([original] if original else [])})
                continue
            segments, no_text = downloaded

        ctx.emit("files", text_path, kind="file", canvas_id=f["id"], title=name,
                 url=f"{ctx.base}/courses/{ctx.cid}/files/{f['id']}",  # a Canvas link, not the download URL
                 content=segments, updated=updated, module=ctx.module_for("file", f["id"]),
                 extra={"extractor": EXTRACTOR_VERSION, "no_text": no_text, "size": f.get("size")},
                 allow_empty=True)  # a scanned PDF has no text, but is still listed
        if original:
            ctx.keep["files"].add(original)


def _skip(ctx: CourseContext, f: dict, name: str, ext: str) -> bool:
    """True for a file we cannot or should not save: a type we cannot read (a .zip), a file
    locked or hidden from students, or one over the size limit."""
    if ext not in SUPPORTED_EXTENSIONS:
        return True
    if f.get("locked") or f.get("hidden") or f.get("locked_for_user") or f.get("hidden_for_user"):
        return True
    if (f.get("size") or 0) > ctx.max_file_bytes:
        log.info("skipping %s (%.1f MB is over the size limit)", name, f["size"] / 1e6)
        return True
    return False


def _local_path(f: dict, name: str, ext: str, folders: dict, taken: set[str]) -> str:
    """Where a file goes in the course folder: files/<its Canvas folder>/<its name>.
    Canvas folder paths start with "course files", which is left out."""
    parts = ((folders.get(f.get("folder_id")) or {}).get("full_name") or "").split("/")[1:]
    directory = "/".join(["files", *(safe_name(p) for p in parts if p)])
    path = f"{directory}/{name}"
    if path.lower() in taken:  # two Canvas names that become the same file name: add the id
        path = f"{directory}/{PurePosixPath(name).stem}-{f['id']}{ext}"
    taken.add(path.lower())
    return path


def _unchanged(ctx: CourseContext, previous: dict | None, file_id, updated: str, original: str | None) -> bool:
    """True when the saved copy is still good: the same Canvas file, not modified since,
    extracted by the current extractor, and its original still on disk."""
    return bool(
        previous and previous.get("canvas_id") == str(file_id) and previous.get("updated_at") == updated
        and previous.get("extractor") == EXTRACTOR_VERSION
        and (original is None or (ctx.cdir / original).exists())
    )


def _download(ctx: CourseContext, f: dict, name: str, original: str | None) -> tuple[list[Segment], bool] | None:
    """Download a file, save the original, and extract its text: (segments, no_text).
    None when it could not be downloaded (hidden, or larger than the limit)."""
    # f["url"] is a Canvas address that redirects to the CDN holding the file
    data = optional(lambda: ctx.client.download(f["url"], ctx.max_file_bytes), f"file {name}", None)
    if data is None:
        return None
    ctx.downloads += 1
    if original:
        write_bytes(ctx.cdir / original, data)
    segments = extract_file(name, data)  # one segment per page or slide, labelled "p. 3", "slide 5"
    if not segments:
        log.info("no extractable text in %s (scanned PDF?)", name)
    return segments, not segments
