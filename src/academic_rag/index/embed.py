"""Step 2 of building the index: turn text into vectors (lists of numbers) whose
closeness means closeness in meaning. Both embedders run on your own CPU, with no
API key: the real model, and a word-hashing stand-in for tests."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import Settings

LOCAL_NAME = "local-minilm-l6-v2"
HASH_DIM = 768

_STOP = set(
    "a an and are as at be but by for from has have he her his i in is it its of on or "
    "she that the their them they this to was we were what when where which who will with you your".split()
)


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def fits(self, text: str) -> bool: ...  # can the model read all of it? (chunk.py splits what can't)


class LocalEmbedder:
    """all-MiniLM-L6-v2 through ONNX runtime (ships with chromadb; no torch), 384
    dimensions. The model (~80 MB) is downloaded once to ~/.cache/chroma/onnx_models/
    (`acad setup` does that ahead of time). It reads at most 256 tokens (word pieces)
    and ignores the rest, so the chunker asks fits() and splits chunks that are longer."""

    name = LOCAL_NAME

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._ef = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._ef(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def fits(self, text: str) -> bool:
        # The model's own tokenizer cuts at 256 tokens and keeps what it cut in `overflowing`.
        return not self._ef.tokenizer.encode(text).overflowing


class HashEmbedder:
    """Deterministic word hashing with no download. Tests/dev only: it matches
    shared words, not meaning."""

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        tokens = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]
        feats = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        v = [math.copysign(math.log1p(abs(x)), x) for x in v]  # sublinear scaling
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def fits(self, text: str) -> bool:
        return True  # no length limit


def embedder_name(settings: Settings) -> str:
    """The backend's name without loading any model (cheap; used by /health)."""
    if settings.embedding_backend == "hash":
        return f"hash-{HASH_DIM}"
    if settings.embedding_backend == "local":
        return LOCAL_NAME
    raise SystemExit(f"Unknown EMBEDDING_BACKEND '{settings.embedding_backend}' (use local or hash).")


def get_embedder(settings: Settings) -> Embedder:
    embedder_name(settings)  # validates the setting
    return HashEmbedder() if settings.embedding_backend == "hash" else LocalEmbedder()
