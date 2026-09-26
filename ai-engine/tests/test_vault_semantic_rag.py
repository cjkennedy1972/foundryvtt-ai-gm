"""Semantic Vault RAG: the player's message in, relevant campaign lore out.

Run:
    cd ai-engine && python -m pytest tests/test_vault_semantic_rag.py -v
"""

import asyncio

from context.loader import CampaignLoader
from vault.indexer import RetrievalResult, SemanticIndexer
from vault.vault_semantic_rag import LoreInjection, SemanticRAG


def _hit(source, cosine, text="lore"):
    return RetrievalResult(text=text, source=source, score=(cosine + 1) / 2)


class FakeIndexer:
    def __init__(self, results):
        self.results, self.queries = results, []

    async def query(self, query_text, top_k=5):
        self.queries.append((query_text, top_k))
        return self.results[:top_k]


def run(coro):
    return asyncio.run(coro)


def test_the_whole_message_is_the_query_not_extracted_words():
    indexer = FakeIndexer([_hit("NPCs/Bram the Innkeeper", 0.72)])
    results = run(SemanticRAG(indexer).inject_lore("I look for the innkeeper", top_k=3))
    # Lowercase, no D&D keyword: the old word extractor found nothing to search.
    assert indexer.queries == [("I look for the innkeeper", 6)]
    assert [r.source for r in results] == ["NPCs/Bram the Innkeeper"]


def test_chatter_below_the_similarity_floor_injects_nothing():
    indexer = FakeIndexer([_hit("Locations/The Void's Maw", 0.51), _hit("Quests/Loot", 0.50)])
    assert run(SemanticRAG(indexer, min_similarity=0.6).inject_lore("brb bathroom")) == []


def test_one_chunk_per_note_up_to_top_k():
    indexer = FakeIndexer([
        _hit("Locations/Peak", 0.80, "a"), _hit("Locations/Peak", 0.78, "b"),
        _hit("NPCs/Morwenna", 0.70), _hit("Quests/Stitch", 0.66), _hit("Quests/Other", 0.65),
    ])
    results = run(SemanticRAG(indexer).inject_lore("We climb the peak", top_k=3))
    assert [r.source for r in results] == ["Locations/Peak", "NPCs/Morwenna", "Quests/Stitch"]
    assert results[0].text == "a"  # the best chunk of a note is the one kept
    assert all(isinstance(r, LoreInjection) and 0 <= r.score <= 1 for r in results)


def test_an_empty_message_does_not_query():
    indexer = FakeIndexer([_hit("x", 0.9)])
    assert run(SemanticRAG(indexer).inject_lore("   ")) == []
    assert indexer.queries == []


def test_a_heading_with_no_body_joins_the_next_section():
    text = "# The Peak\n\n## Details\n\n### Climate\nBitter wind.\n\n### Dangers\nRockfalls."
    chunks = CampaignLoader.__new__(CampaignLoader)._chunk_by_headings(text)
    assert all("\n" in c for c in chunks), chunks
    assert chunks[0].startswith("# The Peak\n\n## Details\n\n### Climate")


class _Vectors:
    """Deterministic non-semantic vectors: this is about what gets indexed."""

    async def embed(self, texts):
        return [[float(len(t) % 5 + 1), 1.0, float(i % 3)] for i, t in enumerate(texts)]

    def get_dimension(self):
        return 3


def test_the_semantic_index_holds_only_the_loaded_campaign_with_titles(tmp_path):
    async def scenario():
        indexer = SemanticIndexer(_Vectors(), index_path=str(tmp_path), cache_enabled=False)
        loader = CampaignLoader.__new__(CampaignLoader)
        loader._semantic_indexer = indexer

        loader._vault_chunks = [("Locations/The Peak", "## Description\n\nWindy."),
                                ("NPCs/Morwenna", "# Morwenna\nWise.")]
        await loader._index_semantic()
        assert indexer.chunks == ["# The Peak\n## Description\n\nWindy.", "# Morwenna\nWise."]

        loader._vault_chunks = [("Quests/Stitch", "## Description\n\nMend it.")]
        await loader._index_semantic()
        assert indexer.chunks == ["# Stitch\n## Description\n\nMend it."]  # the first campaign is gone
        assert [m["source"] for m in indexer.metadata] == ["Quests/Stitch"]
    run(scenario())


def test_a_failed_reindex_empties_the_index_instead_of_keeping_the_old_campaign(tmp_path):
    class Flaky(_Vectors):
        down = False

        async def embed(self, texts):
            if self.down and texts:
                raise ConnectionError("embedding host down")
            return await super().embed(texts)

    async def scenario():
        provider = Flaky()
        indexer = SemanticIndexer(provider, index_path=str(tmp_path), cache_enabled=False)
        loader = CampaignLoader.__new__(CampaignLoader)
        loader._semantic_indexer = indexer
        loader._vault_chunks = [("Locations/The Peak", "## Description\n\nWindy.")]
        await loader._index_semantic()
        assert len(indexer.chunks) == 1

        provider.down = True
        loader._vault_chunks = [("Quests/Stitch", "## Description\n\nMend it.")]
        await loader._index_semantic()
        assert indexer.chunks == []  # not the previous campaign's Peak
    run(scenario())


def test_index_notes_are_titled_by_their_folder(tmp_path):
    async def scenario():
        indexer = SemanticIndexer(_Vectors(), index_path=str(tmp_path), cache_enabled=False)
        loader = CampaignLoader.__new__(CampaignLoader)
        loader._semantic_indexer = indexer
        loader._vault_chunks = [("NPCs/Index", "## Gareth the Barkeep\nPours ale.")]
        await loader._index_semantic()
        assert indexer.chunks == ["# NPCs\n## Gareth the Barkeep\nPours ale."]
    run(scenario())
