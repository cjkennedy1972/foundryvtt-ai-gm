"""Tests for semantic indexing and retrieval.

Tests campaign lore indexing and similarity search.

Run:
    cd ai-engine && python -m pytest tests/test_semantic_indexing.py -v
"""

import importlib.util

import pytest
import asyncio
from pathlib import Path
import tempfile
import shutil

from vault.embeddings import LocalEmbeddings, CachedEmbeddings
from vault.indexer import SemanticIndexer


@pytest.fixture
def temp_index_dir():
    """Create a temporary directory for index files."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# These tests exercise indexing/caching/query mechanics, not retrieval
# quality, so they opt into the non-semantic hash vectors rather than
# requiring the ~2GB sentence-transformers install. Anything that asserts
# results are *semantically* ranked must use requires_real_embeddings below —
# hash vectors would make such an assertion pass without meaning anything.
requires_real_embeddings = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="needs real embeddings: pip install -r requirements-embeddings.txt",
)


@pytest.fixture
def embedding_provider():
    """Create a local embedding provider (hash fallback — see note above)."""
    return LocalEmbeddings(model="all-MiniLM-L6-v2", allow_fallback=True)


@pytest.fixture
def cached_embeddings(embedding_provider, temp_index_dir):
    """Create a cached embedding provider."""
    cache_dir = Path(temp_index_dir) / "embeddings_cache"
    return CachedEmbeddings(embedding_provider, cache_dir=str(cache_dir))


@pytest.fixture
def indexer(cached_embeddings, temp_index_dir):
    """Create a semantic indexer with cached embeddings."""
    index_path = str(Path(temp_index_dir) / "vault_index")
    return SemanticIndexer(cached_embeddings, index_path=index_path)


class TestEmbeddings:
    """Tests for embedding providers."""

    @pytest.mark.asyncio
    async def test_local_embeddings_basic(self, embedding_provider):
        """Local embeddings generate vectors."""
        texts = ["Hello world", "Goodbye world"]
        embeddings = await embedding_provider.embed(texts)

        assert len(embeddings) == 2
        # Each embedding should be a non-empty list
        assert len(embeddings[0]) > 0
        assert len(embeddings[1]) > 0
        # Both should be the same dimension
        assert len(embeddings[0]) == len(embeddings[1])

    @pytest.mark.asyncio
    async def test_embedding_dimension(self, embedding_provider):
        """Embedding dimension is reported correctly."""
        dim = embedding_provider.get_dimension()
        assert dim > 0
        assert dim == 384  # all-MiniLM-L6-v2 uses 384

    @pytest.mark.asyncio
    async def test_cached_embeddings_caches(self, cached_embeddings, temp_index_dir):
        """Cached embeddings persist to disk."""
        texts = ["First text", "Second text"]
        embeddings1 = await cached_embeddings.embed(texts)

        assert len(embeddings1) == 2

        # Check cache files exist
        cache_dir = Path(temp_index_dir) / "embeddings_cache"
        assert cache_dir.exists()
        cache_files = list(cache_dir.glob("*.json"))
        assert len(cache_files) == 2

    @pytest.mark.asyncio
    async def test_cached_embeddings_reuses(self, cached_embeddings):
        """Cached embeddings returns cached values on repeat queries."""
        text = "Unique text for caching"
        embeddings1 = await cached_embeddings.embed([text])
        embeddings2 = await cached_embeddings.embed([text])

        assert embeddings1 == embeddings2


class TestSemanticIndexer:
    """Tests for semantic indexer."""

    @pytest.mark.asyncio
    async def test_index_chunks(self, indexer):
        """Chunks can be added to the index."""
        texts = ["Dragon hoards gold", "Wizard casts spells", "Knight fights enemies"]
        sources = ["lore:1", "lore:2", "lore:3"]

        await indexer.add_chunks(texts, sources)

        assert len(indexer.chunks) == 3
        assert indexer.get_stats()["total_chunks"] == 3

    @pytest.mark.asyncio
    @requires_real_embeddings
    async def test_query_finds_similar(self, indexer):
        """Query ranks semantically related chunks first.

        Skipped without sentence-transformers: under the hash fallback every
        pair of vectors sits at a similar cosine distance, so this assertion
        used to pass on the score>0.7 branch regardless of relevance.
        """
        texts = [
            "A dragon guards a mountain treasure",
            "The knight carried a sword and shield",
            "Magic spells require a wizard to cast",
        ]
        sources = ["settlement:1", "settlement:2", "settlement:3"]

        await indexer.add_chunks(texts, sources)

        # Query for dragon-related content
        results = await indexer.query("dragon treasure gold", top_k=3)

        assert len(results) > 0
        # First result should be dragon-related
        assert "dragon" in results[0].text.lower() or results[0].score > 0.7

    @pytest.mark.asyncio
    async def test_query_empty_index(self, indexer):
        """Query on empty index returns empty."""
        results = await indexer.query("dragon")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_top_k(self, indexer):
        """Query respects top_k parameter."""
        texts = [f"Text {i}" for i in range(10)]
        sources = [f"source:{i}" for i in range(10)]

        await indexer.add_chunks(texts, sources)

        results_5 = await indexer.query("Text", top_k=5)
        results_3 = await indexer.query("Text", top_k=3)

        assert len(results_5) <= 5
        assert len(results_3) <= 3
        assert len(results_3) <= len(results_5)

    @pytest.mark.asyncio
    async def test_index_settlement(self, indexer):
        """Settlement data is indexed with metadata."""
        settlement = {
            "character": "A bustling trade port",
            "buildings": {
                "tavern": {
                    "description": "The Prancing Pony serves ale and stew",
                    "services": ["food", "drink", "lodging"]
                }
            },
            "npcs": {
                "mara": {
                    "occupation": "Tavern keeper",
                    "description": "Stern but fair",
                    "goals": "Keep the tavern profitable"
                }
            }
        }

        await indexer.index_settlement("trader-port", settlement)

        assert indexer.get_stats()["total_chunks"] >= 3  # settlement + building + npc

    @pytest.mark.asyncio
    async def test_persistence(self, temp_index_dir, cached_embeddings):
        """Index persists to disk and reloads."""
        # Create and populate index
        indexer1 = SemanticIndexer(cached_embeddings, index_path=str(Path(temp_index_dir) / "vault1"))
        texts = ["First chunk", "Second chunk"]
        await indexer1.add_chunks(texts, ["src1", "src2"])

        # Load from disk
        indexer2 = SemanticIndexer(cached_embeddings, index_path=str(Path(temp_index_dir) / "vault1"))
        assert len(indexer2.chunks) == 2
        assert indexer2.chunks[0] == "First chunk"

    @pytest.mark.asyncio
    async def test_query_score_range(self, indexer):
        """Query scores are normalized to [0, 1]."""
        texts = ["Dragon", "Knight", "Wizard"]
        sources = ["src1", "src2", "src3"]

        await indexer.add_chunks(texts, sources)

        results = await indexer.query("Dragon")

        for result in results:
            assert 0 <= result.score <= 1

    @pytest.mark.asyncio
    async def test_retrieval_result_fields(self, indexer):
        """Retrieval results have all required fields."""
        await indexer.add_chunks(["Test content"], ["test:source"])

        results = await indexer.query("Test")

        assert len(results) > 0
        result = results[0]
        assert hasattr(result, "text")
        assert hasattr(result, "source")
        assert hasattr(result, "score")
        assert isinstance(result.text, str)
        assert isinstance(result.source, str)
        assert isinstance(result.score, float)


# ── query_batch result ordering ───────────────────────────────────────────
#
# Callers pair results to queries by position, so a result landing in the
# wrong slot answers the wrong question with real lore — silently. The
# method used to append cache hits and insert() the misses at their original
# index, which came apart when the provider returned nothing: the fill loop
# indexed `query_indices[len(x) - len(x)]`, always 0, so every empty went to
# the first uncached slot and pushed cached results one place right.


class _StubProvider:
    """Returns one embedding per query, or a short/empty list on demand."""

    def __init__(self, vectors=None):
        self.vectors = vectors
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.vectors is not None:
            return self.vectors
        return [[float(len(t)), 1.0, 0.0] for t in texts]


def _indexer(temp_index_dir, provider):
    ix = SemanticIndexer(provider, index_path=temp_index_dir)
    ix.chunks = ["a chunk of lore"]
    ix.metadata = [{"source": "lore.md"}]
    ix.embeddings = [[1.0, 1.0, 0.0]]
    ix._norms = [2.0 ** 0.5]
    return ix


def test_query_batch_keeps_a_cached_hit_on_its_own_query(temp_index_dir):
    """The reported failure: provider down, one cache hit in the middle."""
    ix = _indexer(temp_index_dir, _StubProvider(vectors=[]))
    ix.cache.set(f"{ix._normalize_query('q1')}:5", ["CACHED-FOR-q1"])

    out = asyncio.run(ix.query_batch(["q0", "q1", "q2"], top_k=5))

    assert len(out) == 3
    assert out[1] == ["CACHED-FOR-q1"]
    assert out[0] == [] and out[2] == []


def test_query_batch_returns_one_slot_per_query_when_all_miss(temp_index_dir):
    ix = _indexer(temp_index_dir, _StubProvider())

    out = asyncio.run(ix.query_batch(["alpha", "beta"], top_k=3))

    assert len(out) == 2
    assert ix.provider.calls == [["alpha", "beta"]]


def test_query_batch_serves_every_query_from_cache_without_embedding(temp_index_dir):
    ix = _indexer(temp_index_dir, _StubProvider())
    ix.cache.set(f"{ix._normalize_query('a')}:5", ["A"])
    ix.cache.set(f"{ix._normalize_query('b')}:5", ["B"])

    out = asyncio.run(ix.query_batch(["a", "b"], top_k=5))

    assert out == [["A"], ["B"]]
    assert ix.provider.calls == [], "no embedding call is needed when all queries hit cache"


def test_query_batch_only_embeds_the_misses(temp_index_dir):
    ix = _indexer(temp_index_dir, _StubProvider())
    ix.cache.set(f"{ix._normalize_query('cached')}:5", ["C"])

    out = asyncio.run(ix.query_batch(["cached", "fresh"], top_k=5))

    assert ix.provider.calls == [["fresh"]]
    assert out[0] == ["C"]


def test_query_batch_leaves_a_short_provider_reply_empty_rather_than_crashing(temp_index_dir):
    """Two misses but only one embedding back: the second keeps its slot."""
    ix = _indexer(temp_index_dir, _StubProvider(vectors=[[1.0, 1.0, 0.0]]))

    out = asyncio.run(ix.query_batch(["first", "second"], top_k=5))

    assert len(out) == 2
    assert out[1] == []


def test_query_batch_handles_no_queries(temp_index_dir):
    ix = _indexer(temp_index_dir, _StubProvider())

    assert asyncio.run(ix.query_batch([], top_k=5)) == []
