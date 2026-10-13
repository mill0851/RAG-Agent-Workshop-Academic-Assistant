"""Step 3 of building the index: keep the vectors in Chroma, a vector database that
runs inside this process and saves to data/chroma/. query() finds the chunks whose
vectors are closest (by cosine similarity) to a question's vector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb

from ..config import Settings
from .chunk import Chunk

SOURCE_TYPES = ["home", "syllabus", "module", "page", "assignment", "quiz",
                "announcement", "discussion", "file", "upload"]
COLLECTION = "course_materials"
_HNSW = {"hnsw": {"space": "cosine"}}
_BATCH = 500


class IndexMismatch(RuntimeError):
    pass


@dataclass
class Hit:
    text: str
    score: float  # cosine similarity, higher is better
    metadata: dict


class VectorStore:
    """Chroma running inside this process (no server). Vectors are computed by us
    and passed in. The collection records which embedder built it, because vectors
    from two different models cannot be compared."""

    def __init__(self, path: Path, embedder_name: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.embedder_name = embedder_name
        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(
            COLLECTION, configuration=_HNSW, metadata={"embedder": embedder_name})

    def check(self) -> None:
        """Refuse to mix embedders (search and incremental embeds call this)."""
        built_with = (self._col.metadata or {}).get("embedder")
        if built_with and built_with != self.embedder_name:
            raise IndexMismatch(
                f"This index was built with '{built_with}' but the current backend is "
                f"'{self.embedder_name}'. Run `acad embed --rebuild` to re-embed everything."
            )

    def reset(self) -> None:
        """Empty the index; it now belongs to the current embedder."""
        self._client.delete_collection(COLLECTION)
        self._col = self._client.create_collection(
            COLLECTION, configuration=_HNSW, metadata={"embedder": self.embedder_name})

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        for i in range(0, len(chunks), _BATCH):
            part = chunks[i : i + _BATCH]
            self._col.upsert(
                ids=[c.chunk_id for c in part],
                documents=[c.text for c in part],
                embeddings=embeddings[i : i + _BATCH],
                # Chroma metadata may only hold str/int/float/bool
                metadatas=[{k: ("" if v is None else v) for k, v in c.metadata.items()} for c in part],
            )

    def delete_doc(self, doc_id: str) -> None:
        self._col.delete(where={"doc_id": doc_id})

    def delete_course(self, course_id: str) -> None:
        self._col.delete(where={"course_id": course_id})

    def query(self, vector: list[float], k: int = 6, *, course_id: str | None = None,
              source_types: list[str] | None = None) -> list[Hit]:
        if self._col.count() == 0:
            return []
        clauses: list[dict] = []
        if course_id:
            clauses.append({"course_id": course_id})
        if source_types:
            clauses.append({"source_type": {"$in": source_types}})
        where = clauses[0] if len(clauses) == 1 else {"$and": clauses} if clauses else None

        res = self._col.query(
            query_embeddings=[vector],
            n_results=min(k, self._col.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            Hit(text=d, score=1.0 - dist, metadata=m)
            for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def count(self) -> int:
        return self._col.count()


def open_store(settings: Settings, embedder_name: str) -> VectorStore:
    return VectorStore(settings.index_dir, embedder_name)
