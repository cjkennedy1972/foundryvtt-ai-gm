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


# ── chunk size is a budget, not a suggestion ──────────────────────────────
#
# _chunk_text looks for a clean break at or after the character budget. The
# paragraph search is capped at 200 characters past it; the single-newline
# fallback was not capped at all, so it took the next newline wherever it
# was. A note whose text runs on past the budget produced one chunk holding
# everything up to the next line ending.
#
#     one very long line: 2 chunks, longest 40201 chars (budget 3000)
#
# These chunks are what search_vault returns into a prompt, and
# _chunk_by_headings renders them as single-line anchor facts on a 900-char
# budget — 45x over. One of them also skews avgdl for the whole vault, so
# BM25's length normalisation misprices every other chunk alongside it.

def _loader():
    from context.loader import CampaignLoader
    return CampaignLoader.__new__(CampaignLoader)


def _squash(text):
    import re
    return re.sub(r"\s+", "", text)


def test_a_long_line_does_not_become_one_enormous_chunk():
    text = "A" * 200 + "\n" + "B" * 40000 + "\nend"

    chunks = _loader()._chunk_text(text, target_tokens=500)

    longest = max(len(c) for c in chunks)
    assert longest <= 3000 + 200, f"a chunk ran to {longest} chars against a 3000 budget"


def test_an_anchor_fact_chunk_stays_near_its_budget():
    text = "A" * 200 + "\n" + "B" * 40000 + "\nend"

    chunks = _loader()._chunk_by_headings(text, target_tokens=150)

    longest = max(len(c) for c in chunks)
    assert longest <= 900 + 200, f"an anchor fact ran to {longest} chars"


def test_a_nearby_paragraph_break_is_still_preferred():
    """Overshooting slightly to end on a paragraph boundary is the point."""
    text = "x" * 2950 + "\n\n" + "y" * 2000

    chunks = _loader()._chunk_text(text, target_tokens=500)

    assert chunks[0].endswith("x"), "the chunk should run to the paragraph break"
    assert chunks[1].startswith("y")


def test_a_nearby_line_break_is_still_preferred():
    text = "x" * 3050 + "\n" + "y" * 2000

    chunks = _loader()._chunk_text(text, target_tokens=500)

    assert chunks[0].endswith("x")
    assert chunks[1].startswith("y")


def test_chunking_loses_no_content():
    cases = [
        "Para one.\n\n" + "word " * 2000,
        "A" * 200 + "\n" + "B" * 40000 + "\nend",
        "x" * 40000,
        "# Title\n\n" + ("## Section\n\nbody text here.\n\n" * 200),
    ]
    loader = _loader()

    for text in cases:
        joined = "".join(loader._chunk_text(text, target_tokens=500))
        assert _squash(joined) == _squash(text), f"content lost in {text[:20]!r}..."


def test_text_with_no_break_at_all_still_splits_at_the_budget():
    chunks = _loader()._chunk_text("x" * 40000, target_tokens=500)

    assert max(len(c) for c in chunks) <= 3000
    assert len(chunks) == 14


def test_the_nearer_of_two_breaks_wins():
    """With a paragraph break on each side of the budget, taking the farther
    one makes chunks less even for no gain."""
    # x runs 0..2849, a break at 2850 (150 before the 3000 budget), y runs
    # 2852..3049, a second break at 3050 (50 after it).
    text = "x" * 2850 + "\n\n" + "y" * 198 + "\n\n" + "z" * 2000

    chunks = _loader()._chunk_text(text, target_tokens=500)

    assert chunks[0].endswith("y"), "it took the break 150 characters back"
    assert chunks[1].startswith("z")


def test_a_break_far_behind_does_not_produce_a_sliver_chunk():
    """Searching back to the chunk start instead of a window finds a newline
    in the first few characters and emits a chunk of almost nothing."""
    text = "x\n" + "y" * 40000

    chunks = _loader()._chunk_text(text, target_tokens=500)

    assert len(chunks[0]) > 1000, f"first chunk was {len(chunks[0])} chars"
