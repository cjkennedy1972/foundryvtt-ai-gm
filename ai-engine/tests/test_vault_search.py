"""Tests for CampaignLoader's vault-wide BM25 retrieval (search_vault),
which replaced LLMManager._build_anchor_facts's old "first line of the
world file" truncation heuristic.
"""

from context.loader import CampaignLoader, _bm25_rank


def _make_loader():
    loader = CampaignLoader(vault_path="/tmp/nonexistent-vault")
    loader._data = {
        "NPCs/Index": (
            "## Gareth the Barkeep\n"
            "Gareth runs the Sunken Anchor tavern. He is gruff but secretly "
            "kind, and knows every rumor in port.\n\n"
            "## Captain Aldric\n"
            "Aldric commands the city guard. He distrusts outsiders and "
            "suspects the party of smuggling.\n"
        ),
        "Locations/Sunken Anchor": (
            "## The Sunken Anchor\n"
            "A dockside tavern smelling of brine and pipeweed. The "
            "floorboards creak ominously near the cellar door.\n"
        ),
        "World": (
            "## Worldbuilding\n"
            "The kingdom of Veridale has been at war with the Ashen Reach "
            "for a decade.\n"
        ),
        # Shared reference files must be excluded from the lore index.
        "DnD_SRD_v5.2.1_Full_Text": "SRD rules text " * 200,
        "DM_Reference": "DM reference notes.",
    }
    loader._build_vault_index()
    return loader


def test_build_vault_index_excludes_shared_reference_files():
    loader = _make_loader()
    sources = {source for source, _ in loader._vault_chunks}
    assert "DnD_SRD_v5.2.1_Full_Text" not in sources
    assert "DM_Reference" not in sources
    assert "NPCs/Index" in sources


def test_chunk_by_headings_splits_one_npc_per_chunk():
    loader = _make_loader()
    npc_chunks = [text for source, text in loader._vault_chunks if source == "NPCs/Index"]
    assert len(npc_chunks) == 2
    assert npc_chunks[0].startswith("## Gareth the Barkeep")
    assert npc_chunks[1].startswith("## Captain Aldric")


def test_chunk_by_headings_falls_back_for_headingless_text():
    loader = _make_loader()
    chunks = loader._chunk_by_headings("Just a plain paragraph, no headings at all.")
    assert chunks == ["Just a plain paragraph, no headings at all."]


def test_search_vault_ranks_matching_npc_first():
    loader = _make_loader()
    results = loader.search_vault("Gareth barkeep tavern rumors", max_results=3)
    assert results, "expected at least one match"
    assert "Gareth" in results[0]


def test_search_vault_distinguishes_between_npcs():
    loader = _make_loader()
    results = loader.search_vault("Captain Aldric city guard smuggling", max_results=1)
    assert len(results) == 1
    assert "Aldric" in results[0]
    assert "Gareth" not in results[0]


def test_search_vault_empty_query_returns_nothing():
    loader = _make_loader()
    assert loader.search_vault("", max_results=5) == []


def test_search_vault_no_chunks_returns_nothing():
    loader = CampaignLoader(vault_path="/tmp/nonexistent-vault")
    assert loader.search_vault("anything", max_results=5) == []


def test_bm25_rank_prefers_document_matching_rare_term():
    # "aldric" appears in doc 1 only — should outrank the generic doc 0
    # despite doc 0 sharing the common word "guard".
    docs = [
        "the guard stands watch every night at the gate",
        "captain aldric leads the guard with an iron fist",
    ]
    ranked = _bm25_rank("aldric guard", docs, max_results=2)
    assert ranked[0] == 1


def test_bm25_rank_empty_query_or_docs():
    assert _bm25_rank("", ["some text"], max_results=3) == []
    assert _bm25_rank("query", [], max_results=3) == []
