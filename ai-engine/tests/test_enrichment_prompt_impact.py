"""What campaign lore enrichment (#246) puts in front of the model.

Enrichment appends each source's world lore to Worldbuilding.md, writes
numbered extraction notes under Lore/Sources/, and reloads the campaign
mid-session. Seven effects of that on the prompt, each pinned here.

Run:
    cd ai-engine && python -m pytest tests/test_enrichment_prompt_impact.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from config import settings
from context.loader import CampaignLoader
from vault.indexer import RetrievalResult, SemanticIndexer
from vault.vault_semantic_rag import SemanticRAG


def _vault(tmp_path, name="Camp"):
    from campaign.obsidian_sync import get_campaign_folder
    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, name)
    folder.mkdir(parents=True)
    return vault, folder


def _load(vault, name="Camp"):
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load(name))
    return loader


# 1 ── enriched world lore is retrieved, not carried in every prompt ─────────

def test_enriched_sections_leave_the_prompt_and_stay_retrievable(tmp_path):
    vault, folder = _vault(tmp_path)
    (folder / "Worldbuilding.md").write_text(
        "# World\n\n## Geography\nThe Fen of Sighs.\n\n"
        "## From Krynn Sourcebook\n\n### The Dragonarmies\nFive armies under the Highlords.\n")
    loader = _load(vault)

    world = loader.get_world_context_sync()
    assert "Fen of Sighs" in world and "Dragonarmies" not in world
    chunks = [c for s, c in loader._vault_chunks if s == "Worldbuilding"]
    assert any("Dragonarmies" in c for c in chunks)
    assert not any("Fen of Sighs" in c for c in chunks)   # already in the prompt


def test_the_world_context_is_capped_on_a_section_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "world_context_max_tokens", 20)   # 80 chars
    vault, folder = _vault(tmp_path)
    (folder / "Worldbuilding.md").write_text(
        "## Peoples\n" + "Elves and dwarves. " * 3 + "\n\n## Gods\nPaladine and Takhisis.\n")
    loader = _load(vault)

    world = loader.get_world_context_sync()
    assert "Elves" in world and "Paladine" not in world
    assert any("Paladine" in c for s, c in loader._vault_chunks if s == "Worldbuilding")


# 2 ── numbered source notes are titled by their heading ─────────────────────

def test_a_numbered_source_note_is_titled_by_its_heading(tmp_path):
    loader = CampaignLoader.__new__(CampaignLoader)
    loader._data = {"Lore/Sources/krynn-pdf/03": "# Krynn Sourcebook — notes 3/5\n\n## The Dragonarmies\nFive."}
    assert loader._note_title("Lore/Sources/krynn-pdf/03") == "Krynn Sourcebook — notes 3/5"
    assert loader._note_title("NPCs/Index") == "NPCs"
    assert loader._note_title("Locations/The Peak") == "The Peak"


# 3 ── play and canon outrank lore ──────────────────────────────────────────

def test_lore_is_labelled_as_below_canon_and_play():
    from context.reinforcer import ContextReinforcer
    anchored = ContextReinforcer(anchor_facts=["The Highlords rule five armies."]).get_reinforcement()
    assert "must not be contradicted" not in anchored
    assert "except where canon or campaign memory" in anchored


# 4 ── no NPC list rides in the prompt ─────────────────────────────────────

def test_one_note_per_npc_puts_no_npc_list_in_the_system_prompt(tmp_path):
    """Neither one arbitrary NPC's note (the old behaviour) nor a roster that
    grows with the campaign: NPCs reach the model when in play or described."""
    vault, folder = _vault(tmp_path)
    (folder / "NPCs").mkdir()
    (folder / "NPCs" / "Akhviri.md").write_text("# Akhviri\nA long biography " * 20)
    (folder / "campaign.json").write_text(json.dumps({"npcs": [{"name": f"NPC {i}"} for i in range(150)]}))
    assert _load(vault).get_npc_context_sync() == ""


def test_a_single_root_npc_note_is_still_used_whole(tmp_path):
    vault, folder = _vault(tmp_path)
    (folder / "NPCs.md").write_text("## Gareth\nThe barkeep.\n## Aldric\nThe captain.")
    assert "The barkeep." in _load(vault).get_npc_context_sync()


# 5 ── a reload never leaves the loader empty ────────────────────────────────

def test_readers_see_the_old_campaign_until_the_reload_is_complete(tmp_path, monkeypatch):
    vault, folder = _vault(tmp_path)
    (folder / "Worldbuilding.md").write_text("Old world.")
    loader = _load(vault)
    (folder / "Worldbuilding.md").write_text("Enriched world.")

    real_load = CampaignLoader.load
    seen_during = []

    async def slow_load(self, name=""):
        seen_during.append(loader.get_world_context_sync())   # a turn reading mid-reload
        return await real_load(self, name)

    monkeypatch.setattr(CampaignLoader, "load", slow_load)
    asyncio.run(loader.reload())
    assert seen_during == ["## Worldbuilding ##\nOld world."]
    assert "Enriched world." in loader.get_world_context_sync()


# 6 ── lookups skip what the prompt has and what they already returned ───────

def test_canon_and_house_rules_are_not_indexed_again(tmp_path):
    vault, folder = _vault(tmp_path)
    (folder / "Canon.md").write_text("## Fact\nThe king is dead.")
    (folder / "HOUSE_RULES.md").write_text("## Rule\nCrits explode.")
    (folder / "Quests").mkdir()
    (folder / "Quests" / "Crown.md").write_text("## Goal\nFind the crown.")
    sources = {s for s, _ in _load(vault)._vault_chunks}
    assert sources == {"Quests/Crown"}


def test_the_same_meaning_from_two_notes_is_injected_once_and_credited():
    """Different words, one fact: judged by meaning (vectors), not wording."""
    armies, reworded, other = [1.0, 0.0, 0.1], [0.97, 0.05, 0.12], [0.0, 1.0, 0.0]

    class Fake:
        async def query(self, q, top_k=5):
            hit = lambda src, cos, text, vec: RetrievalResult(text=text, source=src, score=(cos + 1) / 2, embedding=vec)
            return [hit("Worldbuilding", 0.80, "Five dragonarmies serve the Highlords of Takhisis", armies),
                    hit("Lore/Sources/krynn/03", 0.79, "Takhisis's generals lead her chromatic hosts", reworded),
                    hit("NPCs/Kansaldi", 0.70, "Kansaldi Fire-Eyes leads the Red Wing", other)]

    results = asyncio.run(SemanticRAG(Fake(), duplicate_similarity=0.8).inject_lore("the armies march", top_k=3))
    assert [r.source for r in results] == ["Worldbuilding", "NPCs/Kansaldi"]
    assert results[0].also_in == ["Lore/Sources/krynn/03"]


def test_shared_words_alone_do_not_make_a_duplicate():
    """The old word-overlap filter would have merged these; their meanings differ."""
    class Fake:
        async def query(self, q, top_k=5):
            return [RetrievalResult("Kalaman's harbour chain guards the city", "Locations/Harbour", 0.9, [1.0, 0.0]),
                    RetrievalResult("Kalaman's harbour burned; the city chain was lost", "History", 0.85, [0.2, 1.0])]
    results = asyncio.run(SemanticRAG(Fake()).inject_lore("the harbour", top_k=3))
    assert len(results) == 2


# 7 ── a reload refreshes anchors and drops the stale scene copies ───────────

def test_refresh_after_reload_rebuilds_the_prompt_from_the_new_files():
    from llm.manager import LLMManager
    loader = MagicMock()
    loader.search_vault.return_value = ["[Worldbuilding] new anchor"]
    loader.get_world_context_sync.return_value = "## Worldbuilding ##\nEnriched world."
    loader.get_npc_context_sync.return_value = ""
    loader.get_house_rules_context_sync.return_value = ""
    loader.get_canon_context_sync.return_value = ""
    manager = LLMManager(campaign_loader=loader)
    manager.set_current_scene("The Fen")
    manager.set_dynamic_world_context("## Worldbuilding ##\nOld world.")   # a scene change's copy
    assert "Old world." in manager.system_prompt

    loader.search_vault.return_value = ["[Worldbuilding] newer anchor"]
    manager.refresh_campaign_context()
    assert "Enriched world." in manager.system_prompt
    assert manager._reinforcer.anchor_facts == {"[Worldbuilding] newer anchor"}
    asyncio.run(manager.close())
