"""Embedding providers for semantic indexing.

Supports OpenAI, Ollama, and local sentence-transformers.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional
import hashlib

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base for embedding generation."""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts."""
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        """Get embedding dimension (e.g., 1536 for OpenAI)."""
        pass


class OpenAIEmbeddings(EmbeddingProvider):
    """OpenAI embeddings API — OpenAI itself, or any compatible server
    (LocalAI, vLLM, llama.cpp) via base_url."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: Optional[str] = None):
        from openai import AsyncOpenAI
        self.model = model
        # The client insists on a key; a keyless local server ignores it.
        # Short timeout: the default is 10 minutes with retries, which would
        # stall startup's probe and every player turn on a down host.
        self._client = AsyncOpenAI(
            api_key=api_key or "unused", base_url=base_url or None, timeout=15, max_retries=1,
        )
        # Known for OpenAI's own models; learned from the first reply otherwise.
        self._dimension = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}.get(model, 0)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        response = await self._client.embeddings.create(input=texts, model=self.model)
        vectors = [item.embedding for item in response.data]
        if vectors and vectors[0]:
            self._dimension = len(vectors[0])
        return vectors

    def get_dimension(self) -> int:
        return self._dimension


class OllamaEmbeddings(EmbeddingProvider):
    """Ollama embedding provider (local LLM)."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url
        self.model = model
        self._dimension = 768  # Most local models use 768

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings via Ollama.

        Uses httpx (already a hard dependency of this project) rather than
        aiohttp, which was imported here but never declared in
        requirements.txt — so this provider could only ever have returned []
        on a clean install.
        """
        import httpx

        results = []
        async with httpx.AsyncClient(timeout=60) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.base_url}/api/embeddings",
                        json={"model": self.model, "prompt": text},
                    )
                except httpx.HTTPError as e:
                    logger.warning(f"Ollama embed request failed: {e}")
                    results.append([])
                    continue
                if resp.status_code == 200:
                    results.append(resp.json().get("embedding", []))
                else:
                    logger.warning(f"Ollama embed failed: {resp.status_code}")
                    results.append([])
        return results

    def get_dimension(self) -> int:
        return self._dimension


MISSING_SENTENCE_TRANSFORMERS = (
    "sentence-transformers is not installed — local embeddings are unavailable. "
    "Install it with: pip install -r requirements-embeddings.txt "
    "(or set VAULT_EMBEDDINGS_ENABLED=false to use keyword search)"
)


class LocalEmbeddings(EmbeddingProvider):
    """Local sentence-transformers embedding provider.

    Raises ImportError when sentence-transformers is missing, rather than
    silently substituting the hash vectors below. That substitution was the
    default behaviour and it is worse than no semantic search at all: hashed
    text carries no semantic relationship, so nearest-neighbour lookups return
    arbitrary chunks and the GM's prompt gets injected with unrelated lore that
    looks retrieved. main.py already handles the ImportError by disabling the
    indexer and falling back to keyword search (BM25), which is honest.

    allow_fallback=True opts into the hash vectors deliberately, for tests that
    exercise indexing/caching mechanics rather than retrieval quality.
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2", allow_fallback: bool = False):
        self.model = model
        self._model_obj = None
        self._dimension = None
        self._allow_fallback = allow_fallback
        self._use_fallback = False

    def _load(self):
        """Import and instantiate the model, or raise a directive ImportError."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(MISSING_SENTENCE_TRANSFORMERS) from e
        return SentenceTransformer(self.model)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using the local model."""
        try:
            if self._model_obj is None:
                self._model_obj = self._load()
                self._dimension = self._model_obj.get_sentence_embedding_dimension()
        except ImportError:
            if not self._allow_fallback:
                raise
            if not self._use_fallback:
                logger.warning(
                    "sentence-transformers not installed — using NON-SEMANTIC hash "
                    "embeddings (allow_fallback=True). Retrieval results are arbitrary."
                )
            self._use_fallback = True
            return self._fallback_embed(texts)

        # encode() is CPU-bound and synchronous: on the event loop it froze
        # every other coroutine (the relay socket, streaming narration) for
        # the length of the embed.
        embeddings = await asyncio.to_thread(self._model_obj.encode, texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]

    def _fallback_embed(self, texts: List[str]) -> List[List[float]]:
        """Hash-based stand-in vectors. Deterministic, and NOT semantic."""
        import hashlib
        dim = 384
        results = []
        for text in texts:
            # Hash text to get consistent but different vectors
            # One sha256 is 32 bytes; chain digests to fill `dim` (h[:dim] on
            # a single digest gave 32-dim vectors while get_dimension() said 384).
            h = b"".join(hashlib.sha256(f"{i}:{text}".encode()).digest() for i in range(dim // 32))
            vec = [float(b) / 256.0 for b in h[:dim]]
            # Normalize
            norm = sum(v**2 for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            results.append(vec)
        return results

    def get_dimension(self) -> int:
        if self._dimension is None:
            try:
                self._dimension = self._load().get_sentence_embedding_dimension()
            except ImportError:
                if not self._allow_fallback:
                    raise
                self._dimension = 384
        return self._dimension


class CachedEmbeddings(EmbeddingProvider):
    """Wrapper that caches embeddings to disk."""

    def __init__(self, provider: EmbeddingProvider, cache_dir: str = ".embedding_cache"):
        self.provider = provider
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, text_hash: str) -> Path:
        """Get cache file path for a text hash."""
        return self.cache_dir / f"{text_hash}.json"

    def _hash_text(self, text: str) -> str:
        """Hash text for cache lookup."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    # The cache is one small JSON file per text, so a sweep over a batch is
    # many blocking open()s. SemanticIndexer.replace_chunks() pushes a whole
    # vault through embed() in batches, and on a warm cache almost every text
    # takes the read path — inline on the event loop that stalled the chat
    # listener and every /api request for the length of a re-index, which
    # happens during play. Both halves run in a worker thread instead: one
    # hop for the whole batch rather than one per text, so the thread-pool
    # overhead does not scale with vault size.

    def _read_cache(self, texts: List[str]):
        """Cache sweep for `texts`. Returns (hits, misses, miss_indices),
        where hits are (index, embedding) pairs. Runs off the event loop."""
        hits = []
        misses: List[str] = []
        miss_indices: List[int] = []
        for i, text in enumerate(texts):
            cache_path = self._get_cache_path(self._hash_text(text))
            if cache_path.exists():
                try:
                    with open(cache_path) as f:
                        hits.append((i, json.load(f)["embedding"]))
                    continue
                except Exception as e:
                    # A corrupt or truncated entry is a miss, not a failure.
                    logger.warning(f"Cache read failed: {e}")
            misses.append(text)
            miss_indices.append(i)
        return hits, misses, miss_indices

    def _write_cache(self, entries) -> None:
        """Persist (text, embedding) pairs. Runs off the event loop."""
        for text, embedding in entries:
            cache_path = self._get_cache_path(self._hash_text(text))
            try:
                with open(cache_path, "w") as f:
                    json.dump({"embedding": embedding}, f)
            except Exception as e:
                logger.warning(f"Cache write failed: {e}")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings, using cache when available."""
        results, uncached_texts, uncached_indices = await asyncio.to_thread(
            self._read_cache, texts
        )

        # Generate embeddings for uncached texts
        if uncached_texts:
            new_embeddings = await self.provider.embed(uncached_texts)
            to_cache = []
            for text, idx, embedding in zip(uncached_texts, uncached_indices, new_embeddings):
                if embedding:
                    to_cache.append((text, embedding))
                results.append((idx, embedding or []))
            if to_cache:
                await asyncio.to_thread(self._write_cache, to_cache)

        # Sort by original index
        results.sort(key=lambda x: x[0])
        return [emb for _, emb in results]

    def get_dimension(self) -> int:
        return self.provider.get_dimension()
