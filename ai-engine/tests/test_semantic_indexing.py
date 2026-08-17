"""Tests for semantic indexing and retrieval.

Tests campaign lore indexing and similarity search.

Run:
    cd ai-engine && python -m pytest tests/test_semantic_indexing.py -v
"""

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


@pytest.fixture
def embedding_provider():
    """Create a local embedding provider."""
    return LocalEmbeddings(model="all-MiniLM-L6-v2")


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
    async def test_query_finds_similar(self, indexer):
        """Query finds similar chunks."""
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
