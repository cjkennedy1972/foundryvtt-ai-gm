"""Embedding providers for semantic indexing.

Supports OpenAI, Ollama, and local sentence-transformers.
"""

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
    """OpenAI embedding provider."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model
        self._dimension = 1536 if model == "text-embedding-3-small" else 3072

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings via OpenAI API."""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key)
            response = await client.embeddings.create(
                input=texts,
                model=self.model,
            )
            return [item.embedding for item in response.data]
        except ImportError:
            logger.error("OpenAI package not installed. Install with: pip install openai")
            return []

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

        embeddings = self._model_obj.encode(texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]

    def _fallback_embed(self, texts: List[str]) -> List[List[float]]:
        """Hash-based stand-in vectors. Deterministic, and NOT semantic."""
        import hashlib
        dim = 384
        results = []
        for text in texts:
            # Hash text to get consistent but different vectors
            h = hashlib.sha256(text.encode()).digest()
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

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings, using cache when available."""
        results = []
        uncached_texts = []
        uncached_indices = []

        # Check cache for each text
        for i, text in enumerate(texts):
            cache_path = self._get_cache_path(self._hash_text(text))
            if cache_path.exists():
                try:
                    with open(cache_path) as f:
                        data = json.load(f)
                        results.append((i, data["embedding"]))
                except Exception as e:
                    logger.warning(f"Cache read failed: {e}")
                    uncached_texts.append(text)
                    uncached_indices.append(i)
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)

        # Generate embeddings for uncached texts
        if uncached_texts:
            new_embeddings = await self.provider.embed(uncached_texts)
            for text, idx, embedding in zip(uncached_texts, uncached_indices, new_embeddings):
                if embedding:
                    # Save to cache
                    cache_path = self._get_cache_path(self._hash_text(text))
                    try:
                        with open(cache_path, "w") as f:
                            json.dump({"embedding": embedding}, f)
                    except Exception as e:
                        logger.warning(f"Cache write failed: {e}")
                results.append((idx, embedding or []))

        # Sort by original index
        results.sort(key=lambda x: x[0])
        return [emb for _, emb in results]

    def get_dimension(self) -> int:
        return self.provider.get_dimension()
