"""Course files: download each PDF, Word file, slide deck or text file, keep the
original next to its extracted text, and skip the download when Canvas says the file
has not changed since the last sync."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from ..course_files import (
    EXTRACTOR_VERSION, SUPPORTED_EXTENSIONS, extract_file, read_document, read_meta, safe_name, write_bytes,
)
from .context import CourseContext, optional

log = logging.getLogger(__name__)

BINARY_EXTENSIONS = {".pdf", ".docx", ".pptx"}  # originals are kept next to their extracted text


def files(ctx: CourseContext) -> None:
    """The Files tab: every file the course shows there."""
    _save_files(ctx, ctx.client.list_files(ctx.cid))


def linked_files(ctx: CourseContext) -> None:
    """Files the course links to from Modules, pages and assignments, fetched one by one
    (many courses hide the Files tab but still link to files)."""
    found = (optional(lambda i=i: ctx.client.get_file(ctx.cid, i), f"file {i}", None)
             for i in sorted(ctx.linked_files, key=int))
    _save_files(ctx, [f for f in found if f])


def _save_files(ctx: CourseContext, files: list[dict]) -> None:
    folders = {f["id"]: f for f in optional(lambda: ctx.client.list_folders(ctx.cid), "folders", [])}
    claimed: set[str] = set()

    for f in files:
        fid = f["id"]
        name = safe_name(f.get("display_name") or f.get("filename") or str(fid), 120)
        ext = PurePosixPath(name.lower()).suffix
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        if f.get("locked") or f.get("hidden") or f.get("locked_for_user") or f.get("hidden_for_user"):
            continue
        if (f.get("size") or 0) > ctx.max_file_bytes:
            log.info("skipping %s (%.1f MB is over the size limit)", name, f["size"] / 1e6)
            continue

        parts = ((folders.get(f.get("folder_id")) or {}).get("full_name") or "").split("/")[1:]
        directory = "/".join(["files", *(safe_name(p) for p in parts if p)])
        rel = f"{directory}/{name}"
        if rel.lower() in claimed:  # two names that sanitize to the same thing
            rel = f"{directory}/{PurePosixPath(name).stem}-{fid}{ext}"
        claimed.add(rel.lower())
        md_rel = rel + ".md"
        orig_rel = rel if ext in BINARY_EXTENSIONS else None

        updated = f.get("modified_at") or f.get("updated_at") or ""
        module = ctx.module_of.get(f"file:{fid}", "")
        prev = read_meta(ctx.cdir / md_rel)
        fresh = bool(
            prev and prev.get("canvas_id") == str(fid) and prev.get("updated_at") == updated
            and prev.get("extractor") == EXTRACTOR_VERSION
            and (orig_rel is None or (ctx.cdir / orig_rel).exists())
        )
        if fresh:  # unchanged in Canvas: reuse what is on disk, no download
            old = read_document(ctx.cdir / md_rel, ctx.data_root)
            segments, no_text = (old.segments if old else []), bool(prev.get("no_text"))
        else:
            data = optional(lambda f=f: ctx.client.download(f["url"], ctx.max_file_bytes), f"file {name}", None)
            if data is None:
                if (ctx.cdir / md_rel).exists():  # could not refresh; keep the last copy
                    ctx.keep["files"].update({md_rel, *([orig_rel] if orig_rel else [])})
                continue
            ctx.downloads += 1
            if orig_rel:
                write_bytes(ctx.cdir / orig_rel, data)
            segments = extract_file(name, data)
            no_text = not segments
            if no_text:
                log.info("no extractable text in %s (scanned PDF?)", name)

        ctx.emit("files", md_rel, "file", fid, name, f"{ctx.base}/courses/{ctx.cid}/files/{fid}",
                 segments, updated, module,
                 extra={"extractor": EXTRACTOR_VERSION, "no_text": no_text, "size": f.get("size")},
                 allow_empty=True)
        if orig_rel:
            ctx.keep["files"].add(orig_rel)
