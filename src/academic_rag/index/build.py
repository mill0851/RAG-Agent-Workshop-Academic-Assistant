"""Build the index: course folders -> chunks -> vectors -> Chroma.

    chunk.py   heading-aware chunks that keep page/slide labels
    embed.py   local CPU embeddings (no API key)
    store.py   the Chroma collection on disk (data/chroma/)
    build.py   embed_courses(): incremental, only changed items are re-embedded

It reads only data/courses/, so it needs no network and no Canvas token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..course_files import (
    CourseStore, Document, count_markdown, iter_documents, read_json, sync_uploads_folder, write_json,
)
from .chunk import Chunk, chunk_document
from .embed import Embedder
from .store import VectorStore

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]  # progress(message, done, total), for the Courses page


@dataclass
class EmbedStats:
    """What one embed did: what `acad embed` prints and POST /embed returns."""

    new: int = 0  # items embedded for the first time
    updated: int = 0  # items embedded again because they changed
    unchanged: int = 0  # items skipped: the same as last time
    removed: int = 0  # items taken out of the index: gone from the course folders
    chunks_written: int = 0
    by_type: dict = field(default_factory=dict)  # items embedded per type ("page": 3, ...)

    def summary(self) -> str:
        """One line: "3 new, 1 updated, 40 unchanged, 0 removed; 25 chunks embedded"."""
        return (f"{self.new} new, {self.updated} updated, {self.unchanged} unchanged, "
                f"{self.removed} removed; {self.chunks_written} chunks embedded")


def load_manifest(path: Path) -> dict:
    """doc_id -> {hash, course_id, source_type, title, chunks}: what is in the index."""
    return read_json(path) or {}


def embed_courses(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
    *,
    rebuild: bool = False,
    course_ids: list[str] | None = None,
    progress: ProgressFn | None = None,
    batch_chunks: int = 64,
) -> EmbedStats:
    """Embed the saved course folders. Each item's content hash is kept in a manifest,
    so re-running only re-embeds items that changed and drops items that disappeared."""
    progress = progress or (lambda msg, done, total: None)
    stats = EmbedStats()
    wanted = {str(c) for c in course_ids or []}

    # 1. The course folders to embed (all of them, or the ones asked for)
    dirs: list[tuple[Path, dict]] = []
    for d in CourseStore(settings.courses_dir).course_dirs():
        meta = read_json(d / "course.json") or {}
        if "id" not in meta:
            log.warning("skipping %s: course.json has no id", d.name)
            continue
        if wanted and str(meta["id"]) not in wanted:
            continue
        dropped = sync_uploads_folder(d, meta)  # files you copied into <course>/uploads/ yourself
        if dropped["added"] or dropped["removed"]:
            log.info("uploads folder of %s: added %s, removed %s", d.name, dropped["added"], dropped["removed"])
        dirs.append((d, meta))

    # 2. What is in the index already, or nothing for a rebuild
    manifest = load_manifest(settings.manifest_path)
    if rebuild and not wanted:  # full rebuild: also the way to switch embedding backends
        store.reset()
        manifest = {}
    else:
        store.check()
        if rebuild:  # rebuild only the named courses
            for cid in wanted:
                store.delete_course(cid)
            manifest = {k: v for k, v in manifest.items() if v["course_id"] not in wanted}

    total = sum(count_markdown(d) for d, _ in dirs)
    done = 0
    seen: set[str] = set()
    pending: list[tuple[Document, list[Chunk], bool]] = []  # (item, its chunks, already in the index)
    pending_chunks = 0

    def flush() -> None:
        """Embed the pending items' chunks in one batch, store them, and save the manifest."""
        nonlocal pending, pending_chunks
        if not pending:
            return
        texts = [c.embed_text for _, chunks, _ in pending for c in chunks]
        vectors = embedder.embed_documents(texts)
        pos = 0
        for doc, chunks, existed in pending:
            if existed:
                store.delete_doc(doc.doc_id)  # the old chunk count may differ from the new one
                stats.updated += 1
            else:
                stats.new += 1
            store.upsert(chunks, vectors[pos : pos + len(chunks)])
            pos += len(chunks)
            stats.chunks_written += len(chunks)
            stats.by_type[doc.source_type] = stats.by_type.get(doc.source_type, 0) + 1
            manifest[doc.doc_id] = {
                "hash": doc.content_hash, "course_id": doc.course_id,
                "source_type": doc.source_type, "title": doc.title, "chunks": len(chunks),
            }
        write_json(settings.manifest_path, manifest)  # progress survives an interrupted run
        pending, pending_chunks = [], 0

    # 3. Every item: unchanged ones are skipped, the rest are chunked and embedded in batches
    for cdir, meta in dirs:
        for doc in iter_documents(cdir, settings.data_dir, meta.get("name")):
            done += 1
            seen.add(doc.doc_id)
            progress(f"Embedding {meta.get('name')}: {doc.title}", done, total)
            prev = manifest.get(doc.doc_id)
            if prev and prev["hash"] == doc.content_hash:
                stats.unchanged += 1
                continue
            chunks = chunk_document(doc, settings.chunk_chars, settings.chunk_overlap, embedder.fits)
            if not chunks:  # became empty (or never had text): make sure nothing stale is indexed
                if prev:
                    store.delete_doc(doc.doc_id)
                    del manifest[doc.doc_id]
                    stats.removed += 1
                continue
            pending.append((doc, chunks, bool(prev)))
            pending_chunks += len(chunks)
            if pending_chunks >= batch_chunks:
                flush()
    flush()

    # 4. Prune: items whose file is gone, and (on a full run) whole courses whose folder is gone.
    scanned = {str(m["id"]) for _, m in dirs}
    for doc_id, m in list(manifest.items()):
        gone = doc_id not in seen and m["course_id"] in scanned
        course_gone = not wanted and m["course_id"] not in scanned
        if gone or course_gone:
            store.delete_doc(doc_id)
            del manifest[doc_id]
            stats.removed += 1
    write_json(settings.manifest_path, manifest)
    progress("Embedding finished", total, total)
    return stats
