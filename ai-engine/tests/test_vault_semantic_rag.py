"""Tests for semantic vault RAG — entity extraction and lore injection."""

import asyncio
import pytest
from vault.vault_semantic_rag import EntityExtractor, SemanticRAG, LoreInjection
from vault.embeddings import LocalEmbeddings
from vault.indexer import SemanticIndexer


class TestEntityExtractor:
    """Entity extraction from narrative."""

    def test_extract_capitalized_names(self):
        """Extract proper nouns."""
        ex = EntityExtractor()
        entities = ex.extract_entities("Mara the Tavern Keeper met Grendel at the Redmarch.")
        assert "Mara" in entities
        assert "Tavern" in entities
        assert "Keeper" in entities
        assert "Grendel" in entities
        assert "Redmarch" in entities

    def test_extract_dnd_keywords(self):
        """Extract D&D-specific words."""
        ex = EntityExtractor()
        entities = ex.extract_entities("A wizard and a dragon fought in the dungeon with magic.")
        assert "wizard" in entities
        assert "dragon" in entities
        assert "dungeon" in entities
        assert "magic" in entities

    def test_extract_mixed(self):
        """Extract both names and keywords."""
        ex = EntityExtractor()
        entities = ex.extract_entities("The lich Valygar guards the treasure vault.")
        assert "lich" in entities
        assert "Valygar" in entities
        assert "treasure" in entities
        assert "vault" in entities

    def test_extract_empty(self):
        """Handle text with no entities."""
        ex = EntityExtractor()
        entities = ex.extract_entities("the quick brown fox jumps")
        assert len(entities) == 0

    def test_extract_case_insensitive_dnd(self):
        """D&D keywords matched case-insensitively."""
        ex = EntityExtractor()
        entities = ex.extract_entities("A WIZARD and a Lich battle the Dragon.")
        # Capitalized versions are proper nouns
        assert "Wizard" in entities or "wizard" in entities
        assert "Lich" in entities or "lich" in entities
        assert "Dragon" in entities or "dragon" in entities


class TestSemanticRAG:
    """Semantic injection with vault queries."""

    @pytest.mark.asyncio
    async def test_inject_lore_basic(self, tmp_path):
        """Query vault for extracted entities."""
        # Mechanics test (shape/score-range/dedup), so the non-semantic hash
        # fallback is enough — see tests/test_semantic_indexing.py for the rationale.
        embeddings = LocalEmbeddings(allow_fallback=True)
        indexer = SemanticIndexer(embeddings, index_path=str(tmp_path / "index"))
        rag = SemanticRAG(indexer)

        # Index some settlement data
        await indexer.add_chunks(
            texts=[
                "Mara is a halfling tavern keeper in Redmarch.",
                "Redmarch is a trade town on the crossroads.",
                "The wizard Grendel lives in a tower north of Redmarch."
            ],
            sources=[
                "settlement:redmarch:npc:mara",
                "settlement:redmarch:description",
                "settlement:redmarch:wizard"
            ]
        )

        # Inject lore for narrative with entities
        narrative = "The party arrives at Redmarch and meets Mara."
        results = await rag.inject_lore(narrative, top_k=2)

        assert len(results) > 0
        assert all(isinstance(r, LoreInjection) for r in results)
        assert all(0 <= r.score <= 1 for r in results)
        assert all(r.source for r in results)

    @pytest.mark.asyncio
    async def test_inject_lore_deduplication(self, tmp_path):
        """Dedup results by source."""
        embeddings = LocalEmbeddings(allow_fallback=True)
        indexer = SemanticIndexer(embeddings, index_path=str(tmp_path / "index"))
        rag = SemanticRAG(indexer)

        # Index multiple results for same entity
        await indexer.add_chunks(
            texts=[
                "Mara is a tavern keeper.",
                "Mara likes to tell stories.",
                "Mara serves the best ale."
            ],
            sources=[
                "settlement:redmarch:npc:mara:desc1",
                "settlement:redmarch:npc:mara:desc2",
                "settlement:redmarch:npc:mara:desc3"
            ]
        )

        narrative = "We meet Mara at the tavern."
        results = await rag.inject_lore(narrative, top_k=5)

        # Even with multiple sources, should deduplicate
        sources = [r.source for r in results]
        assert len(sources) == len(set(sources)), "Duplicate sources found"

    @pytest.mark.asyncio
    async def test_inject_lore_debounce(self, tmp_path):
        """Debounce queries for same entity."""
        embeddings = LocalEmbeddings(allow_fallback=True)
        indexer = SemanticIndexer(embeddings, index_path=str(tmp_path / "index"))
        rag = SemanticRAG(indexer, debounce_seconds=0.1)

        await indexer.add_chunks(
            texts=["Dragon information."],
            sources=["creature:dragon:desc"]
        )

        # First query
        narrative1 = "The dragon attacked."
        results1 = await rag.inject_lore(narrative1, top_k=3)
        assert len(results1) > 0

        # Second query immediately (should hit cache, no new search)
        narrative2 = "The dragon fled."
        results2 = await rag.inject_lore(narrative2, top_k=3)

        # Results should be from cache (same)
        assert len(results2) > 0

    def test_lore_injection_fields(self):
        """LoreInjection dataclass has required fields."""
        injection = LoreInjection(
            text="Sample lore",
            source="settlement:testville:description",
            score=0.95
        )
        assert injection.text == "Sample lore"
        assert injection.source == "settlement:testville:description"
        assert injection.score == 0.95

    @pytest.mark.asyncio
    async def test_empty_narrative_no_entities(self, tmp_path):
        """Handle narratives with no extractable entities."""
        embeddings = LocalEmbeddings(allow_fallback=True)
        indexer = SemanticIndexer(embeddings, index_path=str(tmp_path / "index"))
        rag = SemanticRAG(indexer)

        narrative = "the party walks down the road"
        results = await rag.inject_lore(narrative)
        assert len(results) == 0

    def test_cache_clear(self, tmp_path):
        """Clear debounce and dedup caches."""
        embeddings = LocalEmbeddings(allow_fallback=True)
        indexer = SemanticIndexer(embeddings, index_path=str(tmp_path / "index"))
        rag = SemanticRAG(indexer)

        # Populate caches
        rag._last_queries["dragon"] = 123.456
        rag._dedup_cache["wizard"] = [LoreInjection("text", "src", 0.9)]

        rag.clear_cache()

        assert len(rag._last_queries) == 0
        assert len(rag._dedup_cache) == 0
