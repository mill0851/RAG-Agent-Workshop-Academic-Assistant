"""Step 2 of building the index: turn text into vectors (lists of numbers) whose
closeness means closeness in meaning. Both embedders run on your own CPU, with no
API key: the real model, and a word-hashing stand-in for tests.

    LocalEmbedder    all-MiniLM-L6-v2: 384 numbers per text, by meaning
    HashEmbedder     word hashing: shared words, not meaning (tests and offline use)
    get_embedder()   the one EMBEDDING_BACKEND in .env asks for
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import Settings

LOCAL_NAME = "local-minilm-l6-v2"  # recorded in the index, which refuses vectors of another model
HASH_DIM = 768  # the hash embedder's vector length

# Words too common to say anything about meaning: the hash embedder ignores them
_STOP = set(
    "a an and are as at be but by for from has have he her his i in is it its of on or "
    "she that the their them they this to was we were what when where which who will with you your".split()
)


class Embedder(Protocol):
    """What every embedder can do."""

    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, for the chunks being stored."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """The vector of one question, compared with the stored ones."""
        ...

    def fits(self, text: str) -> bool:
        """Can the model read all of it? (chunk.py splits what can't)"""
        ...


# ------------------------------------------------------------- the real one
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
        """One 384-number vector per text, each of length 1."""
        return [list(map(float, v)) for v in self._ef(texts)]

    def embed_query(self, text: str) -> list[float]:
        """A question's vector: the same model, so it can be compared with the chunks'."""
        return self.embed_documents([text])[0]

    def fits(self, text: str) -> bool:
        """True if the model reads all of it: its 256 tokens are not exceeded."""
        # The model's own tokenizer cuts at 256 tokens and keeps what it cut in `overflowing`.
        return not self._ef.tokenizer.encode(text).overflowing


# --------------------------------------------------- the stand-in for tests
class HashEmbedder:
    """Deterministic word hashing with no download. Tests/dev only: it matches
    shared words, not meaning."""

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        """Each word and each pair of neighbouring words adds +1 or -1 to one of `dim`
        slots, chosen by its hash; the counts are softened by a logarithm and the vector
        scaled to length 1."""
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
        """One vector per text."""
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        """A question's vector, made the same way."""
        return self._vec(text)

    def fits(self, text: str) -> bool:
        """Always True: word hashing has no length limit."""
        return True  # no length limit


# ----------------------------------------------------------------- choosing
def embedder_name(settings: Settings) -> str:
    """The backend's name without loading any model (cheap; used by /health)."""
    if settings.embedding_backend == "hash":
        return f"hash-{HASH_DIM}"
    if settings.embedding_backend == "local":
        return LOCAL_NAME
    raise SystemExit(f"Unknown EMBEDDING_BACKEND '{settings.embedding_backend}' (use local or hash).")


def get_embedder(settings: Settings) -> Embedder:
    """The embedder EMBEDDING_BACKEND in .env asks for: local (the model) or hash."""
    embedder_name(settings)  # validates the setting
    return HashEmbedder() if settings.embedding_backend == "hash" else LocalEmbedder()
