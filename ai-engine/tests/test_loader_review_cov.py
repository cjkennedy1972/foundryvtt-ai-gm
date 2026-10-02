"""Behavioral coverage for context/loader.py (review pass)."""

import json
from pathlib import Path

import pytest

from config import settings
from context.loader import CampaignLoader, _as_float, _bm25_rank
from utils.token_counter import CHARS_PER_TOKEN


class _Registry:
    """Minimal NPC registry double that records what the loader hands it."""

    def __init__(self):
        self.npcs = {}
        self.personality = {}

    def get_npc_by_name(self, name):
        return next((r for r in self.npcs.values() if r.npc_name == name), None)

    def register_npc(self, npc_id, npc_name, description="", alignment=None, disposition=0.0):
        rec = type("Rec", (), {})()
        rec.npc_id, rec.npc_name, rec.description = npc_id, npc_name, description
        rec.alignment, rec.disposition, rec.notes = alignment, disposition, []
        self.npcs[npc_id] = rec
        return rec

    def set_npc_personality(self, npc_id, traits):
        self.personality[npc_id] = traits


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "DnD_SRD_v5.2.1_Full_Text.txt").write_text(
        "Grappling rules\n\nA grapple needs a free hand.\n\nOpportunity attacks use reactions.")
    (tmp_path / "DM_Reference.md").write_text("dm ref")
    return tmp_path


def test_as_float_falls_back_on_garbage():
    assert _as_float("0.5", 9) == 0.5
    assert _as_float("abc", 9) == 9
    assert _as_float(None, 3.0) == 3.0


def test_bm25_empty_inputs_and_unmatched_query():
    assert _bm25_rank("", ["a"], 3) == []
    assert _bm25_rank("a", [], 3) == []
    assert _bm25_rank("zzz", ["alpha beta"], 3) == []


@pytest.mark.asyncio
async def test_load_missing_vault_returns_empty(tmp_path):
    loader = CampaignLoader(vault_path=str(tmp_path / "nope"))
    assert await loader.load("Anything") == {}


@pytest.mark.asyncio
async def test_load_reads_campaign_folder_manifest_and_caches(vault):
    camp = vault / "Campaigns" / "Krynn"
    (camp / "NPCs").mkdir(parents=True)
    (camp / "NPCs" / "Index.md").write_text("# Key NPCs\n## Tika\nA fighter.")
    (camp / "campaign.json").write_text(json.dumps({"scenes": [{"name": "A"}, "junk"], "npcs": []}))
    loader = CampaignLoader(vault_path=str(vault))
    data = await loader.load("Krynn")
    assert set(data) == {"DnD_SRD_v5.2.1_Full_Text", "DM_Reference", "NPCs/Index"}
    assert loader.current_campaign_name == "Krynn"
    assert loader.campaign_scenes == [{"name": "A"}]  # non-dict entries dropped
    assert loader.srd_chunks  # SRD chunked
    # cached: a new file is invisible until reload()
    (camp / "Late.md").write_text("late")
    assert "Late" not in await loader.load("Krynn")
    assert "Late" in await loader.reload()
    assert loader.current_campaign_name == "Krynn"


@pytest.mark.asyncio
async def test_load_legacy_flat_layout_and_bad_inputs(vault):
    flat = vault / "Old"
    flat.mkdir()
    (flat / "Story.md").write_text("tale")
    (flat / "bad.md").write_bytes(b"\xff\xfe\x00bad")
    (flat / "campaign.json").write_text("{not json")
    loader = CampaignLoader(vault_path=str(vault))
    data = await loader.load("Old")
    assert data["Story"] == "tale"
    assert "bad" not in data  # undecodable skipped, others still load
    assert loader._campaign_data == {}
    assert loader.campaign_scenes == []


@pytest.mark.asyncio
async def test_load_manifest_not_a_dict_ignored(vault):
    flat = vault / "Arr"
    flat.mkdir()
    (flat / "campaign.json").write_text("[1,2]")
    loader = CampaignLoader(vault_path=str(vault))
    await loader.load("Arr")
    assert loader._campaign_data == {}


@pytest.mark.asyncio
async def test_load_unknown_campaign_still_returns_shared(vault):
    loader = CampaignLoader(vault_path=str(vault))
    data = await loader.load("Ghost")
    assert set(data) == {"DnD_SRD_v5.2.1_Full_Text", "DM_Reference"}


class _Indexer:
    def __init__(self, fail=False):
        self.chunks, self.calls, self.fail = [], [], fail

    async def replace_chunks(self, texts, sources):
        self.calls.append((list(texts), list(sources)))
        if self.fail and texts:
            raise RuntimeError("embed down")
        self.chunks = list(texts)


@pytest.mark.asyncio
async def test_semantic_index_titles_chunks_and_skips_when_unchanged(vault):
    camp = vault / "Campaigns" / "C"
    camp.mkdir(parents=True)
    (camp / "Tavern.md").write_text("## Description\nDim and smoky.")
    idx = _Indexer()
    loader = CampaignLoader(vault_path=str(vault), semantic_indexer=idx)
    await loader.load("C")
    assert idx.calls[-1] == (["# Tavern\n## Description\nDim and smoky."], ["Tavern"])
    n = len(idx.calls)
    await loader._index_semantic()  # identical chunks: no re-embed
    assert len(idx.calls) == n


@pytest.mark.asyncio
async def test_semantic_index_failure_empties_index_not_stale(vault):
    camp = vault / "Campaigns" / "C"
    camp.mkdir(parents=True)
    (camp / "Tavern.md").write_text("## Tavern\nsmoky")
    idx = _Indexer(fail=True)
    idx.chunks = ["OLD CAMPAIGN LORE"]
    loader = CampaignLoader(vault_path=str(vault), semantic_indexer=idx)
    await loader.load("C")
    assert idx.chunks == []


def test_note_title_variants():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"Lore/Sources/s/03": "# Dragon Lore\nx", "Lore/Sources/s/04": "no heading",
                    "05": "bare"}
    assert loader._note_title("NPCs/Index") == "NPCs"
    assert loader._note_title("Index") == "Index"
    assert loader._note_title("Lore/Sources/s/03") == "Dragon Lore"
    assert loader._note_title("Lore/Sources/s/04") == "s"
    assert loader._note_title("05") == "05"
    assert loader._note_title("Locations/Inn") == "Inn"


def test_world_parts_cap_splits_at_boundary_and_keeps_sourced(monkeypatch):
    monkeypatch.setattr(settings, "world_context_max_tokens", 10)
    limit = 10 * CHARS_PER_TOKEN
    para1 = "A" * (limit - 10)
    md = f"{para1}\n\n{'B' * 50}\n\n## From Book\nsourced lore"
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"Worldbuilding": md}
    head, rest = loader._world_parts()
    assert head == para1  # cut at the paragraph boundary, not mid-fact
    assert "B" * 50 in rest and "sourced lore" in rest
    assert loader.get_world_context_sync() == f"## Worldbuilding ##\n{para1}"
    # no boundary under the cap: hard cut at the limit
    loader._data = {"Worldbuilding": "C" * (limit + 20)}
    head, rest = loader._world_parts()
    assert head == "C" * limit and rest == "C" * 20


def test_world_key_prefers_root_and_skips_sources():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"Lore/Sources/x/World of Krynn": "s", "My World": "m"}
    assert loader._world_key() == "My World"
    loader._data = {"Lore/Sources/x/World of Krynn": "s"}
    assert loader._world_key() is None
    assert loader._world_parts() == ("", "")
    assert loader.get_world_context_sync() == ""
    loader._data["Worldbuilding"] = "w"
    assert loader._world_key() == "Worldbuilding"


def test_vault_index_excludes_prompt_notes_and_world_overflow():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"Canon": "## Fact\nthe sky is green", "HOUSE_RULES": "## R\nx",
                    "Worldbuilding": "## Intro\nauthored\n\n## From Book\nsourced dragons",
                    "Story/One": "## S\nplot"}
    loader._build_vault_index()
    srcs = {s for s, _ in loader._vault_chunks}
    assert srcs == {"Worldbuilding", "Story/One"}
    world_text = " ".join(t for s, t in loader._vault_chunks if s == "Worldbuilding")
    assert "sourced dragons" in world_text and "authored" not in world_text


def test_chunk_by_headings_lead_carry_and_oversize():
    loader = CampaignLoader(vault_path="/x")
    text = "lead text\n\n## Details\n### Sub\nbody\n\n## Big\n" + ("word " * 400)
    chunks = loader._chunk_by_headings(text, target_tokens=50)
    assert chunks[0] == "lead text"
    assert chunks[1].startswith("## Details") and "### Sub" in chunks[1]  # bodyless heading carried
    assert len(chunks) > 3  # the oversized section was split further


def test_search_vault_empty_and_prefixed_results():
    loader = CampaignLoader(vault_path="/x")
    assert loader.search_vault("anything") == []
    loader._vault_chunks = [("NPCs/Tika", "Tika fights"), ("Story/A", "dragons attack")]
    assert loader.search_vault("dragons") == ["[Story/A] dragons attack"]


@pytest.mark.asyncio
async def test_search_srd_ranks_by_keyword_hits_and_empty():
    loader = CampaignLoader(vault_path="/x")
    loader._srd_chunks = ["grapple rules", "grapple and shove rules", "nothing"]
    out = await loader.search_srd("grapple shove", max_results=1)
    assert out == "## SRD Reference ##\ngrapple and shove rules"
    out2 = await loader.search_srd("grapple", max_results=5)
    assert out2.count("---") == 1 and "nothing" not in out2
    assert await loader.search_srd("zzz") == ""


@pytest.mark.asyncio
async def test_section_getters():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"NPCs/Index": "sub", "NPCs": "root npcs", "Campaign State": "st",
                    "Character Hooks": "h", "DM_Reference": "dm", "Session 1": "plan",
                    "Canon": "c1", "Encounters": "x"}
    assert await loader.get_npc_context() == "## NPCs ##\nroot npcs"  # subfolder note skipped
    assert await loader.get_campaign_state() == "## Campaign State ##\nst"
    assert await loader.get_character_hooks() == "## Character Hooks ##\nh"
    assert await loader.get_dm_reference() == "## DM Reference ##\ndm"
    assert await loader.get_session_plan() == "## Session Plan ##\nplan"
    assert loader.get_canon_context_sync() == "## Canon / Established Facts ##\nc1"
    assert await loader.get_world_context() == ""
    assert loader.get_all_loaded_data() is not loader._data
    empty = CampaignLoader(vault_path="/x")
    assert empty.get_npc_context_sync() == ""
    assert await empty.get_campaign_state() == ""
    assert await empty.get_character_hooks() == ""
    assert await empty.get_dm_reference() == ""
    assert await empty.get_session_plan() == ""
    assert empty.get_canon_context_sync() == ""
    assert empty.get_house_rules_context_sync() == ""


def test_scene_briefing_picks_story_over_location_and_strips_map():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {
        "Locations/The Caves": "Location text",
        "Story/Scene - The Caves": "tags: a, b\nIntro\n## Map\nPAINT A MAP\n## Mood\nDamp",
    }
    out = loader.get_scene_briefing("  the caves ")
    assert out == "Intro\n## Mood\nDamp"
    assert loader.get_scene_briefing("") == ""
    assert loader.get_scene_briefing("nowhere") == ""
    loader._data = {"Locations/The Caves": "Location text", "Story/Cave Mouth": "story"}
    assert loader.get_scene_briefing("caves") == "Location text"
    loader._data = {"Notes/The Caves": "x"}
    assert loader.get_scene_briefing("The Caves") == ""  # only Story/ and Locations/ count


def test_encounter_context_matches_only_scene():
    loader = CampaignLoader(vault_path="/x")
    assert loader.get_encounter_context_for_scene("A") == ""
    loader._data = {"Encounters": "**Scene:** The Crypt\nghouls\n---\n**Scene:** Docks\nthugs\n---\nno scene\n"}
    out = loader.get_encounter_context_for_scene("the crypt")
    assert "ghouls" in out and "thugs" not in out and out.startswith("## Encounter Briefs")
    assert loader.get_encounter_context_for_scene("Elsewhere") == ""
    assert loader.get_encounter_context_for_scene("") == ""


def test_register_npcs_from_manifest_fields_and_dedupe():
    loader = CampaignLoader(vault_path="/x")
    loader._campaign_data = {"npcs": [
        {"name": "Tika Waylan", "description": "d", "alignment": "NG", "disposition": "bad",
         "personality": ["bold", 7], "role": "fighter", "first_appearance": "Act 1"},
        {"name": "Raistlin", "personality": {"quirks": ["cough"]}},
        {"name": "Tika Waylan"}, {"name": "  "}, "junk",
    ]}
    reg = _Registry()
    assert loader.register_vault_npcs(reg) == 2
    tika = reg.npcs["tika_waylan"]
    assert tika.disposition == 0.0 and tika.alignment == "NG"
    assert reg.personality["tika_waylan"] == {"traits": ["bold", "7"]}
    assert tika.notes == ["Role: fighter", "First Appearance: Act 1"]
    assert reg.personality["raistlin"] == {"quirks": ["cough"]}


def test_register_npcs_from_notes_layouts():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {
        "NPCs/Tika": "# Tika\n## Description\nx\n## Stat Block\ny",  # one NPC per note
        "NPCs/Index": "# Key NPCs\n## Raistlin\nmage\n## Backstory X\nz\n## Goals\nq\n## " + "L" * 70,
        "NPCs/Dup": "# Tika\ndup",
        "Story/Not": "## Ignored\n",
    }
    reg = _Registry()
    n = loader.register_vault_npcs(reg)
    names = {r.npc_name for r in reg.npcs.values()}
    assert names == {"Tika", "Raistlin", "Backstory X"}  # sections and >60-char heading dropped
    assert n == 3
    assert reg.npcs["raistlin"].description.startswith("Raistlin")
    # bold-only fallback layout
    loader._data = {"NPCs/Cast": "**Gareth:** barkeep"}
    reg2 = _Registry()
    assert loader.register_vault_npcs(reg2) == 1 and "gareth" in reg2.npcs


def test_register_npcs_from_notes_skips_already_registered():
    loader = CampaignLoader(vault_path="/x")
    loader._data = {"NPCs/Tika": "# Tika\nx"}
    reg = _Registry()
    reg.register_npc("t", "Tika")
    assert loader.register_vault_npcs(reg) == 0


@pytest.mark.asyncio
async def test_load_custom_campaign_links_validates_and_rejects_traversal(vault):
    (vault / "Lore").mkdir()
    (vault / "Lore" / "a.md").write_text("alpha")
    (vault.parent / "secret.md").write_text("secret")
    loader = CampaignLoader(vault_path=str(vault))
    res = await loader.load_custom_campaign(
        "My Camp", ["Dungeons_and_Dragons/Lore/a.md", "../secret.md", "Lore/missing.md"])
    assert res["linked_files"] == ["a.md"]
    assert loader._data["a.md"] == "alpha"
    assert (vault / "My Camp" / "a.md").is_symlink()
    # re-link: symlink exists already -> falls back to copy without raising
    res2 = await loader.load_custom_campaign("My Camp", ["Lore/a.md"])
    assert res2["linked_files"] == ["a.md"]
    # hostile campaign name cannot escape the vault
    res3 = await loader.load_custom_campaign("../../evil", [])
    assert Path(res3["folder"]).resolve().is_relative_to(vault.resolve())


@pytest.mark.asyncio
async def test_save_campaign_writes_sanitized_json(tmp_path, monkeypatch):
    import context.loader as mod
    fake = tmp_path / "pkg" / "context" / "loader.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(mod, "__file__", str(fake))
    loader = CampaignLoader(vault_path=str(tmp_path))
    assert await loader.save_campaign("../x/Camp", {"a": "b"}) is True
    written = list((tmp_path / "pkg" / "campaigns").glob("*.json"))
    assert len(written) == 1 and json.loads(written[0].read_text()) == {"a": "b"}
