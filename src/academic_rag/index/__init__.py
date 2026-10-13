"""The core of RAG, in pipeline order: chunk.py cuts items into chunks, embed.py turns
text into vectors, store.py keeps them in Chroma, build.py runs those steps for every
course, and search.py answers a question with the nearest chunks. See README.md."""

from .build import EmbedStats, embed_courses, load_manifest
from .chunk import Chunk, chunk_document, split_text
from .embed import Embedder, HashEmbedder, LocalEmbedder, embedder_name, get_embedder
from .search import Passage, boost_exact_matches, format_passages, resolve_course, search
from .store import SOURCE_TYPES, IndexMismatch, VectorStore, open_store
