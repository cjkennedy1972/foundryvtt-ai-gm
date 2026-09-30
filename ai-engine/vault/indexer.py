"""Semantic indexer for campaign lore — builds and queries searchable vector index.

Chunks campaign documents, generates embeddings, stores in HNSW index.
"""

import json
import math
import logging
import re
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from operator import mul
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import asyncio

logger = logging.getLogger(__name__)


class QueryCache:
    """Thread-safe LRU cache for query results with TTL.

    ponytail: simple dict-based LRU with time tracking. No external deps.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: OrderedDict[str, Tuple[List, float]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> Optional[List]:
        """Get cached result if exists and not expired."""
        with self.lock:
            if key not in self.cache:
                return None

            results, timestamp = self.cache[key]
            if time.time() - timestamp > self.ttl_seconds:
                del self.cache[key]
                return None

            # Move to end (LRU)
            self.cache.move_to_end(key)
            return results

    def set(self, key: str, results: List) -> None:
        """Cache results with current timestamp."""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)

            self.cache[key] = (results, time.time())

            # Evict oldest if over size
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def clear(self) -> None:
        """Clear cache."""
        with self.lock:
            self.cache.clear()

    def stats(self) -> Dict:
        """Cache statistics."""
        with self.lock:
            return {
                "size": len(self.cache),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds
            }


@dataclass
class RetrievalResult:
    """Single search result."""
    text: str
    source: str  # e.g., "settlement:redmarch", "npc:mara"
    score: float  # similarity score 0-1
    # The chunk's vector, so callers can compare results by meaning.
    embedding: Optional[List[float]] = None


class SemanticIndexer:
    """Builds and queries a semantic index of campaign lore."""

    def __init__(self, embedding_provider, index_path: str = ".vault_index",
                 cache_enabled: bool = True, cache_size: int = 100,
                 cache_ttl_seconds: int = 300):
        self.provider = embedding_provider
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)

        self.chunks: List[str] = []
        self.metadata: List[Dict] = []
        self.embeddings: List[List[float]] = []
        self.index = None
        self._norms: List[float] = []

        # Query result cache
        self.cache = QueryCache(max_size=cache_size, ttl_seconds=cache_ttl_seconds) if cache_enabled else None
        self._load_or_init_index()

    def _normalize_query(self, query_text: str) -> str:
        """Normalize query for cache key: lowercase, remove punctuation.

        ponytail: simple normalization to deduplicate similar queries.
        Helps cache hit rate on variant phrasings.
        """
        # Lowercase
        text = query_text.lower()
        # Remove punctuation but keep spaces
        text = re.sub(r'[^\w\s]', '', text)
        # Collapse whitespace
        text = ' '.join(text.split())
        return text

    def _load_or_init_index(self):
        """Load existing index or create new one."""
        chunks_file = self.index_path / "chunks.json"
        embeddings_file = self.index_path / "embeddings.json"

        if chunks_file.exists() and embeddings_file.exists():
            try:
                with open(chunks_file) as f:
                    data = json.load(f)
                    self.chunks = data.get("chunks", [])
                    self.metadata = data.get("metadata", [])

                with open(embeddings_file) as f:
                    self.embeddings = json.load(f)

                self._build_hnsw_index()
                logger.info(f"Loaded {len(self.chunks)} cached chunks")
                return
            except Exception as e:
                logger.warning(f"Failed to load cached index: {e}")

        logger.info("Starting fresh semantic index")

    def _build_hnsw_index(self):
        """Build HNSW index from embeddings (and the norms linear search uses)."""
        self._norms = [math.sqrt(sum(map(mul, e, e))) if e else 0.0 for e in self.embeddings]
        if not self.embeddings:
            self.index = None  # nothing to index yet; built on the first add
            return
        try:
            import hnswlib

            # From the vectors themselves: a provider's declared dimension is
            # a guess for any model it doesn't know (an OpenAI-compatible
            # server can serve anything), and a wrong one breaks add_items.
            dim = len(self.embeddings[0])
            self.index = hnswlib.Index(space="cosine", dim=dim)
            self.index.init_index(max_elements=len(self.embeddings), ef_construction=200, M=16)

            if self.embeddings:
                self.index.add_items(self.embeddings, list(range(len(self.embeddings))))

            logger.info(f"Built HNSW index with {len(self.embeddings)} vectors")
        except ImportError:
            logger.warning("hnswlib not installed. Using fallback linear search.")
            self.index = None  # Use linear search fallback

    async def index_settlement(self, settlement_id: str, settlement_data: Dict):
        """Index a settlement and its NPCs."""
        chunks = []

        # Index settlement description
        desc = settlement_data.get("character", "")
        if desc:
            chunks.append({
                "text": f"Settlement {settlement_id}: {desc}",
                "source": f"settlement:{settlement_id}:description"
            })

        # Index buildings
        for building_id, building in settlement_data.get("buildings", {}).items():
            building_desc = building.get("description", "") or building.get("services", "")
            if building_desc:
                chunks.append({
                    "text": f"Building {building_id} in {settlement_id}: {building_desc}",
                    "source": f"settlement:{settlement_id}:building:{building_id}"
                })

        # Index NPCs
        for npc_id, npc in settlement_data.get("npcs", {}).items():
            npc_desc = npc.get("description", "") or npc.get("occupation", "")
            goals = npc.get("goals", "")
            text_parts = [f"NPC {npc_id} ({npc.get('occupation', 'unknown')})"]
            if npc_desc:
                text_parts.append(npc_desc)
            if goals:
                text_parts.append(f"Goals: {goals}")

            chunks.append({
                "text": " ".join(text_parts),
                "source": f"settlement:{settlement_id}:npc:{npc_id}"
            })

        await self.add_chunks([c["text"] for c in chunks], [c["source"] for c in chunks])

    async def add_chunks(self, texts: List[str], sources: List[str]):
        """Add text chunks to the index."""
        if not texts:
            return

        # Generate embeddings
        embeddings = await self.provider.embed(texts)

        # Add to index
        for text, source, embedding in zip(texts, sources, embeddings):
            if embedding:
                self.chunks.append(text)
                self.metadata.append({"source": source})
                self.embeddings.append(embedding)

        # Rebuild index
        self._build_hnsw_index()

        # Persist to disk
        self._save_index()
        logger.info(f"Indexed {len(texts)} chunks (total: {len(self.chunks)})")

    async def replace_chunks(self, texts: List[str], sources: List[str], batch_size: int = 64):
        """Make the index hold exactly these chunks. add_chunks only ever
        appends, so a second campaign's lore sat beside the first's and an
        edited note kept its old text too. Embeds in batches; the provider's
        disk cache makes texts it has seen before free."""
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            embeddings.extend(await self.provider.embed(texts[i:i + batch_size]))
        kept = [(t, s, e) for t, s, e in zip(texts, sources, embeddings) if e]
        self.chunks = [t for t, _, _ in kept]
        self.metadata = [{"source": s} for _, s, _ in kept]
        self.embeddings = [e for _, _, e in kept]
        self._build_hnsw_index()
        self._save_index()
        if self.cache:
            self.cache.clear()
        logger.info(f"Index now holds {len(self.chunks)} chunks")

    async def query(self, query_text: str, top_k: int = 5) -> List[RetrievalResult]:
        """Search index for similar passages.

        Checks cache first (normalized query key), falls back to embedding + search.
        """
        if not self.chunks:
            return []

        # Check cache
        cache_key = f"{self._normalize_query(query_text)}:{top_k}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit: {query_text[:50]}")
                return cached

        # Embed query
        embeddings = await self.provider.embed([query_text])
        if not embeddings or not embeddings[0]:
            return []

        query_embedding = embeddings[0]

        # Search
        if self.index:
            results = self._search_hnsw(query_embedding, top_k)
        else:
            # Off the event loop: pure-Python cosine over every chunk took
            # 0.37s for 2000 chunks x 2560 dims, freezing the relay socket and
            # delaying the turn's first narration. ponytail: install hnswlib
            # (or vectorise with numpy) if that latency itself starts to matter.
            results = await asyncio.to_thread(self._search_linear, query_embedding, top_k)

        # Cache results
        if self.cache:
            self.cache.set(cache_key, results)

        return results

    async def query_batch(self, queries: List[str], top_k: int = 5) -> List[List[RetrievalResult]]:
        """Batch query multiple strings at once.

        Embeds all queries together (3x faster than sequential), then searches.
        Returns list of result lists, one per input query.
        """
        if not self.chunks or not queries:
            return [[] for _ in queries]

        # One slot per query, filled in place. Callers match results to
        # queries by position, so the slot must be the query's own index:
        # appending cache hits and then insert()-ing the misses put a cached
        # result under the wrong query whenever the embedding call came back
        # empty (the fill loop indexed query_indices[len(x) - len(x)], which
        # is always 0, so every empty landed at the first uncached slot).
        results_list: List[List[RetrievalResult]] = [[] for _ in queries]

        # Check cache for each query
        cache_keys = []
        queries_to_embed = []
        query_indices = []

        for i, query_text in enumerate(queries):
            cache_key = f"{self._normalize_query(query_text)}:{top_k}"
            cache_keys.append(cache_key)

            if self.cache:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    results_list[i] = cached
                    continue

            queries_to_embed.append(query_text)
            query_indices.append(i)

        # If all queries hit cache, return early
        if not queries_to_embed:
            return results_list

        # Embed uncached queries in batch. Anything the provider does not
        # return an embedding for keeps its empty slot, which is also what a
        # provider that is down produces for the whole batch.
        embeddings = await self.provider.embed(queries_to_embed)

        # Search each embedding
        for i, query_idx in enumerate(query_indices):
            if i < len(embeddings) and embeddings[i]:
                if self.index:
                    results = self._search_hnsw(embeddings[i], top_k)
                else:
                    results = await asyncio.to_thread(self._search_linear, embeddings[i], top_k)

                # Cache this result
                if self.cache:
                    self.cache.set(cache_keys[query_idx], results)

                results_list[query_idx] = results

        return results_list

    def _search_hnsw(self, query_embedding: List[float], top_k: int) -> List[RetrievalResult]:
        """Search using HNSW index."""
        try:
            labels, distances = self.index.knn_query([query_embedding], k=min(top_k, len(self.chunks)))

            results = []
            for label, distance in zip(labels[0], distances[0]):
                if label < len(self.chunks):
                    # Convert distance to similarity (cosine distance -> similarity)
                    similarity = 1 - (distance / 2)  # Normalize from [-1, 1] to [0, 1]
                    results.append(RetrievalResult(
                        text=self.chunks[label],
                        source=self.metadata[label].get("source", "unknown"),
                        embedding=self.embeddings[label],
                        score=max(0, similarity)  # Clamp to [0, 1]
                    ))

            return results
        except Exception as e:
            logger.error(f"HNSW search failed: {e}")
            return []

    def _search_linear(self, query_embedding: List[float], top_k: int) -> List[RetrievalResult]:
        """Linear search fallback when HNSW unavailable: cosine against every
        chunk, using the norms cached by _build_hnsw_index.

        ponytail: pure Python, ~5x faster than recomputing both norms per
        chunk as this used to; install hnswlib (or vectorise with numpy) if a
        vault grows past what that covers.
        """
        norm_q = math.sqrt(sum(map(mul, query_embedding, query_embedding)))
        if not norm_q:
            return []
        dim = len(query_embedding)
        scores = []
        for i, (embedding, norm_e) in enumerate(zip(self.embeddings, self._norms)):
            if norm_e and len(embedding) == dim:
                cosine = sum(map(mul, query_embedding, embedding)) / (norm_q * norm_e)
                scores.append((i, (cosine + 1) / 2))  # reported in [0, 1]

        scores.sort(key=lambda x: x[1], reverse=True)
        return [
            RetrievalResult(
                text=self.chunks[idx],
                source=self.metadata[idx].get("source", "unknown"),
                embedding=self.embeddings[idx],
                score=max(0, min(1, score)),
            )
            for idx, score in scores[:top_k]
        ]

    def _save_index(self):
        """Persist index to disk."""
        try:
            chunks_file = self.index_path / "chunks.json"
            embeddings_file = self.index_path / "embeddings.json"

            with open(chunks_file, "w") as f:
                json.dump({
                    "chunks": self.chunks,
                    "metadata": self.metadata
                }, f)

            with open(embeddings_file, "w") as f:
                json.dump(self.embeddings, f)

            logger.info(f"Saved index with {len(self.chunks)} chunks")
        except Exception as e:
            logger.error(f"Failed to save index: {e}")

    def get_stats(self) -> Dict:
        """Get index statistics."""
        stats = {
            "total_chunks": len(self.chunks),
            "embedding_dim": len(self.embeddings[0]) if self.embeddings else self.provider.get_dimension(),
            "provider": self.provider.__class__.__name__,
            "index_path": str(self.index_path)
        }

        # Add cache stats if enabled
        if self.cache:
            stats["cache"] = self.cache.stats()

        return stats

    def clear_cache(self) -> None:
        """Clear query result cache."""
        if self.cache:
            self.cache.clear()
            logger.info("Query cache cleared")
